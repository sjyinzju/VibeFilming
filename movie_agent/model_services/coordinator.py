"""Resource admission and lifecycle over existing jobs, never a second workflow."""

import asyncio
from collections import Counter
from contextlib import suppress
import json
from pathlib import Path
from time import perf_counter

from movie_agent.domain import (EventEnvelope, EventType, JobStatus, ProviderErrorType,
                               ProviderResult, utc_now)
from movie_agent.media import ModelServiceStatus
from movie_agent.providers.base import ProviderFailure
from movie_agent.providers.media import normalize_media_provider_error
from .resources import (GiB, LeaseStatus, ModelResidency, ResourceLease, ResourceSnapshot,
                        ResourceObservation, ResourceRuntimeSettings, SchedulerDecision)

ACTIVE = {LeaseStatus.ACQUIRED, LeaseStatus.EXECUTING, LeaseStatus.UNCERTAIN}


class ResourceAdmissionWait(ProviderFailure):
    def __init__(self, reason):
        super().__init__(reason, ProviderErrorType.RESOURCE_EXHAUSTED)


class RuntimeCoordinator:
    """One shared coordinator per serving worker / Spark, with crash-safe lease state."""

    def __init__(self, manager, telemetry, *, settings=None, state_path=None):
        self.manager, self.telemetry = manager, telemetry
        self.settings = settings or ResourceRuntimeSettings()
        self.state_path = Path(state_path) if state_path else None
        self.leases: dict[str, ResourceLease] = {}
        self.decisions: list[SchedulerDecision] = []
        self.observations: list[ResourceObservation] = []
        self.last_snapshot: ResourceSnapshot | None = None
        self._lock = asyncio.Lock()
        self._events = {}
        self._idle_since = {}
        self._resident_credit = {}
        self._ready = []
        self._last_service = None
        self._batch_count = 0
        self._oom_penalties = {}
        self._before = {}
        self._startup = {}
        self._warmup = {}
        self._base_credit = {}
        self._isolated = {}
        if self.state_path and self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            for item in state.get("leases", []):
                lease = ResourceLease.model_validate(item)
                if "inference_started" not in item and lease.status in {LeaseStatus.EXECUTING, LeaseStatus.UNCERTAIN}:
                    lease.inference_started = True
                if lease.status in ACTIVE:
                    lease.status = LeaseStatus.UNCERTAIN
                self.leases[lease.lease_id] = lease
            self.decisions = [SchedulerDecision.model_validate(x) for x in state.get("decisions", [])]
            self.observations = [ResourceObservation.model_validate(x) for x in state.get("observations", [])]
            self._oom_penalties = state.get("oom_penalties", {})
            # Process restart invalidates physical residency credits, not remote execution locks.
        self._update_profiles()

    def bind(self, project_id, bus, trace_id):
        self._events[project_id] = (bus, trace_id)

    def restore_jobs(self, jobs, events=()):
        """Import pre-P4A remote evidence before making any eviction decision."""
        for job in jobs:
            uncertain = job.remote_completion_uncertain or "remote_completion_uncertain" in (job.failure_reason or "")
            interrupted = job.remote_status in {JobStatus.QUEUED, JobStatus.RUNNING} and job.status != JobStatus.SUCCEEDED
            if not (uncertain or interrupted):
                continue
            # A later committed explicit recovery supersedes this shot's old remote lock.
            if job.shot_id and any(other.status == JobStatus.SUCCEEDED and other.shot_id == job.shot_id
                    and other.task == job.task and other.created_at > job.created_at for other in jobs):
                continue
            sid = job.model_service_id or self.service_for(job.provider_id or job.provenance.provider_id)
            if not sid or any(x.project_id == job.project_id and x.job_id == job.job_id for x in self.leases.values()):
                continue
            prompt_id = job.remote_prompt_id
            for event in events:
                if event.job_id == job.job_id and event.payload.get("provider_execution_graph"):
                    prompt_id = event.payload["provider_execution_graph"].get("remote_prompt_id") or prompt_id
            lease = ResourceLease(project_id=job.project_id, job_id=job.job_id, service_id=sid,
                reserved_memory_bytes=self.reservation(sid), resource_class=job.resource_class,
                status=LeaseStatus.UNCERTAIN, remote_prompt_id=prompt_id)
            lease.inference_started = True
            self.leases[lease.lease_id] = lease
        self._save()

    def service_for(self, provider_id):
        return next((s.descriptor.service_id for s in self.manager.all()
                     if s.descriptor.runtime_profile and
                     s.descriptor.runtime_profile.provider_id == provider_id), None)

    def active_leases(self, service_id=None):
        return [x for x in self.leases.values() if x.status in ACTIVE
                and (service_id is None or x.service_id == service_id)]

    def _emit(self, kind, project_id, payload, job_id=None):
        if project_id in self._events:
            bus, trace_id = self._events[project_id]
            bus.emit(EventEnvelope(event_type=kind, project_id=project_id, trace_id=trace_id,
                                   job_id=job_id, payload=payload))

    def _save(self):
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        state = {"leases": [x.model_dump(mode="json") for x in self.leases.values()],
                 "decisions": [x.model_dump(mode="json") for x in self.decisions[-100:]],
                 "observations": [x.model_dump(mode="json") for x in self.observations[-100:]],
                 "oom_penalties": self._oom_penalties}
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.state_path)

    def _audit(self, kind, payload):
        if self.state_path:
            with self.state_path.with_suffix(".history.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"kind": kind, "value": payload}, ensure_ascii=False) + "\n")

    async def snapshot(self, project_id=None):
        snapshot = await self.telemetry.snapshot()
        snapshot.system_reserve_bytes = int(self.settings.system_reserve_gib * GiB)
        snapshot.safety_margin_bytes = int(self.settings.safety_margin_gib * GiB)
        snapshot.active_services = [s.descriptor.service_id for s in self.manager.all()
            if s.descriptor.status not in {ModelServiceStatus.STOPPED, ModelServiceStatus.FAILED}]
        snapshot.active_resource_leases = [x.model_copy(deep=True) for x in self.active_leases()]
        self.last_snapshot = snapshot
        if project_id:
            self._emit(EventType.RESOURCE_SNAPSHOT, project_id, snapshot.model_dump(mode="json"))
        return snapshot

    def reservation(self, service_id):
        profile = self.manager.get(service_id).descriptor.runtime_profile
        return max(profile.estimated_peak_bytes, profile.observed_peak_bytes or 0) + self._oom_penalties.get(service_id, 0)

    def can_admit(self, service_id, snapshot):
        target = self.manager.get(service_id).descriptor
        profile = target.runtime_profile
        if service_id in {'tts','music'} and profile.estimated_peak_bytes == 0:
            return False, 'Audio runtime profile requires real smoke telemetry before admission'
        leases = self.active_leases()
        if target.status in {ModelServiceStatus.DRAINING, ModelServiceStatus.STOPPING, ModelServiceStatus.BUSY}:
            return False, "service is busy or draining"
        if len(self.active_leases(service_id)) >= profile.concurrency_limit:
            return False, "provider concurrency limit"
        if leases and (profile.requires_exclusive_runtime or any(
                self.manager.get(x.service_id).descriptor.runtime_profile.requires_exclusive_runtime for x in leases)):
            return False, "exclusive runtime lease"
        minimum_free = target.metadata.get('minimum_cold_start_free_bytes')
        if minimum_free and target.status != ModelServiceStatus.READY:
            free = snapshot.diagnostics.get('free_unified_memory_bytes')
            if free is None or free < minimum_free:
                return False, 'insufficient physically free unified memory for cold-start allocator; reclaim idle residency'
        headroom = snapshot.system_reserve_bytes + snapshot.safety_margin_bytes
        required = self.reservation(service_id)
        # MemAvailable already excludes resident allocations. Subtract only unmaterialized
        # reservations here. Credits are measured in isolation and reset on stop/restart.
        per_service = Counter()
        for lease in leases:
            per_service[lease.service_id] += lease.reserved_memory_bytes
        outstanding = sum(max(0, value - self._resident_credit.get(sid, 0))
                          for sid, value in per_service.items())
        incremental = max(0, required - self._resident_credit.get(service_id, 0))
        if snapshot.available_unified_memory_bytes < headroom + outstanding + incremental:
            return False, "insufficient available unified memory after reserve and safety margin"
        # Separate total envelope: resident idle services OR reserved active services.
        envelope = sum(max(per_service.get(s.descriptor.service_id, 0),
                           self._resident_credit.get(s.descriptor.service_id, 0))
                       for s in self.manager.all() if s.descriptor.service_id != service_id)
        if envelope + required + headroom > snapshot.total_unified_memory_bytes:
            return False, "unified memory reservation envelope exhausted"
        return True, "unified memory and concurrency admission passed"

    def set_ready_jobs(self, jobs):
        self._ready = list(jobs)

    def order_ready(self, jobs):
        """Caller supplies DAG/gate/chain-eligible jobs only. Priority always wins."""
        self.set_ready_jobs(jobs)
        if not jobs:
            return []
        priority = max(x.priority for x in jobs)
        peers = [x for x in jobs if x.priority == priority]
        oldest = min(peers, key=lambda x: (x.created_at, x.job_id))
        def key(job):
            sid = self.service_for(job.provider_id)
            warm = sid is not None and self.manager.get(sid).descriptor.status == ModelServiceStatus.READY
            fairness = self._batch_count >= self.settings.max_affinity_batch and job.job_id == oldest.job_id
            return (-job.priority, -int(fairness), -int(warm), job.created_at, job.job_id)
        return sorted(jobs, key=key)

    async def _evict(self, service, project_id):
        sid = service.descriptor.service_id
        if self.active_leases(sid) or service.descriptor.status in {
                ModelServiceStatus.BUSY, ModelServiceStatus.DRAINING, ModelServiceStatus.STARTING,
                ModelServiceStatus.WARMING, ModelServiceStatus.STOPPING}:
            return False
        if not await service.idle():
            return False
        for active in self.active_leases():
            self._isolated[active.lease_id] = False
        service.descriptor.status = ModelServiceStatus.DRAINING
        service.descriptor.residency = ModelResidency.EVICTING
        self._service_event(service, project_id)
        try:
            await asyncio.wait_for(self.manager.stop(sid), self.settings.service_stop_timeout)
        except BaseException:
            service.descriptor.status = ModelServiceStatus.FAILED
            self._service_event(service, project_id)
            raise
        self._resident_credit.pop(sid, None)
        self._idle_since.pop(sid, None)
        self._service_event(service, project_id)
        return True

    def _service_event(self, service, project_id):
        self._audit("service_status", {"service_id": service.descriptor.service_id,
            "project_id": project_id, "status": service.descriptor.status.value,
            "residency": service.descriptor.residency.value, "timestamp": utc_now().isoformat()})
        self._emit(EventType.MODEL_SERVICE_STATUS_CHANGED, project_id,
                   {"service_id": service.descriptor.service_id, "status": service.descriptor.status.value,
                    "residency": service.descriptor.residency.value})

    def _decision(self, job, sid, snapshot, actions, admitted, reason):
        decision = SchedulerDecision(project_id=job.project_id, job_id=job.job_id,
            selected_service_id=sid, resource_snapshot=snapshot,
            required_reservation_bytes=self.reservation(sid), actions=actions, admitted=admitted, reason=reason)
        self.decisions.append(decision)
        self._save()
        self._audit("decision", decision.model_dump(mode="json"))
        self._emit(EventType.SCHEDULER_DECISION, job.project_id, decision.model_dump(mode="json"), job.job_id)
        return decision

    def _wait(self, job, sid, reason):
        self._decision(job, sid, self.last_snapshot, [], False, reason)
        self._emit(EventType.RESOURCE_ADMISSION_WAIT, job.project_id, {"reason": reason}, job.job_id)
        raise ResourceAdmissionWait(reason)

    async def acquire(self, job, provider_id=None):
        sid = self.service_for(provider_id or job.provider_id)
        if sid is None or not self.settings.enabled:
            return None
        if sid in {'tts','music'} and self.manager.get(sid).descriptor.runtime_profile.estimated_peak_bytes == 0:
            self._wait(job,sid,'Audio runtime profile requires real smoke telemetry before admission')
        if job.status in {JobStatus.WAITING_HUMAN, JobStatus.BLOCKED, JobStatus.SUCCEEDED,
                           JobStatus.CANCELLED, JobStatus.FAILED}:
            self._wait(job, sid, "job is not eligible for production execution")
        for previous in list(self.active_leases()):
            if previous.status == LeaseStatus.UNCERTAIN:
                await self.reconcile(previous.lease_id)
        async with self._lock:
            if any(x.job_id == job.job_id and x.project_id == job.project_id for x in self.active_leases()):
                self._wait(job, sid, "job already has an active or uncertain remote lease")
            if job.remote_completion_uncertain:
                self._wait(job, sid, "remote completion must be reconciled before admission")
            if job.continuity_chain_id and any(x.project_id == job.project_id and
                    x.continuity_chain_id == job.continuity_chain_id for x in self.active_leases()):
                self._wait(job, sid, "continuity chain has an active lease")
            for service in self.manager.all():
                await service.status()
            snapshot = await self.snapshot(job.project_id)
            actions = []
            admitted, reason = self.can_admit(sid, snapshot)
            if not admitted and reason in {"provider concurrency limit", "exclusive runtime lease", "service is busy or draining"}:
                self._wait(job, sid, reason)
            if not admitted:
                self._emit(EventType.RESOURCE_PRESSURE, job.project_id, {"reason": reason}, job.job_id)
                affinity = Counter(self.service_for(x.provider_id) for x in self._ready)
                candidates = sorted(self.manager.all(), key=lambda s: (
                    affinity[s.descriptor.service_id],
                    -(s.descriptor.runtime_profile.estimated_resident_bytes /
                      max(1, s.descriptor.runtime_profile.startup_cost_seconds))))
                for service in candidates:
                    if service.descriptor.status in {ModelServiceStatus.STOPPED, ModelServiceStatus.FAILED}:
                        continue
                    # An idle target with UNKNOWN residency may need a clean restart baseline.
                    if await self._evict(service, job.project_id):
                        actions.append(f"drain/stop:{service.descriptor.service_id}")
                        snapshot = await self.snapshot(job.project_id)
                        admitted, reason = self.can_admit(sid, snapshot)
                        if admitted:
                            break
            self._decision(job, sid, snapshot, actions, admitted, reason)
            if not admitted:
                self._emit(EventType.RESOURCE_ADMISSION_WAIT, job.project_id, {"reason": reason}, job.job_id)
                raise ResourceAdmissionWait(reason)
            lease = ResourceLease(project_id=job.project_id, job_id=job.job_id, service_id=sid,
                continuity_chain_id=job.continuity_chain_id,
                reserved_memory_bytes=self.reservation(sid), resource_class=job.resource_class)
            overlapping = self.active_leases()
            self._isolated[lease.lease_id] = not overlapping
            for other in overlapping:
                self._isolated[other.lease_id] = False
            self.leases[lease.lease_id] = lease
            self._save()  # Reserve before startup so concurrent admissions cannot race.
            self._emit(EventType.RESOURCE_LEASE_ACQUIRED, job.project_id, lease.model_dump(mode="json"), job.job_id)
            self._before[lease.lease_id] = snapshot
            self._base_credit[lease.lease_id] = self._resident_credit.get(sid, 0)
            service = self.manager.get(sid)
            start = perf_counter()
            service.startup_seconds = service.warmup_seconds = 0
            if service.descriptor.status != ModelServiceStatus.READY:
                service.descriptor.status = ModelServiceStatus.STARTING
                self._service_event(service, job.project_id)
            try:
                await self.manager.ensure_ready(sid, timeout=self.settings.service_start_timeout)
                self._startup[lease.lease_id] = getattr(service, "startup_seconds", perf_counter() - start)
                self._warmup[lease.lease_id] = getattr(service, "warmup_seconds", 0)
                after_start = await self.snapshot(job.project_id)
                # Isolated startup allocations supply a conservative lower bound on residency.
                if len(self.active_leases()) == 1:
                    allocated = max(0, snapshot.available_unified_memory_bytes - after_start.available_unified_memory_bytes)
                    self._resident_credit[sid] = min(service.descriptor.runtime_profile.estimated_resident_bytes,
                                                    self._resident_credit.get(sid, 0) + allocated)
                if after_start.available_unified_memory_bytes < after_start.system_reserve_bytes + after_start.safety_margin_bytes:
                    raise ResourceAdmissionWait("startup consumed safety headroom; inference was not submitted")
                self._service_event(service, job.project_id)
            except BaseException as error:
                oom = False
                with suppress(ProviderFailure, TimeoutError):
                    oom = await service.oom_killed()
                if oom or getattr(error, "error_type", None) == ProviderErrorType.RESOURCE_EXHAUSTED_OOM:
                    self._record_oom(job, lease, after_start if 'after_start' in locals() else snapshot)
                # A timed-out SSH/start call may leave the container loading remotely.
                # Keep that reservation until death/idle can actually be confirmed.
                definite = oom
                with suppress(ProviderFailure, TimeoutError):
                    definite = (await service.status()) in {ModelServiceStatus.STOPPED, ModelServiceStatus.FAILED}
                await self.release(lease, uncertain=not definite, invalidated=definite)
                for cache in (self._before, self._base_credit, self._isolated, self._startup, self._warmup):
                    cache.pop(lease.lease_id, None)
                raise
            return lease

    def mark_submitted(self, job, prompt_id):
        for lease in self.active_leases():
            if lease.job_id == job.job_id and lease.project_id == job.project_id:
                lease.remote_prompt_id = prompt_id
        self._save()

    async def release(self, lease, *, uncertain=False, invalidated=False):
        if lease.status not in ACTIVE:
            return
        lease.status = (LeaseStatus.UNCERTAIN if uncertain else
                        LeaseStatus.INVALIDATED if invalidated else LeaseStatus.RELEASED)
        lease.released_at = None if uncertain else utc_now()
        service = self.manager.get(lease.service_id)
        if uncertain:
            service.descriptor.status = ModelServiceStatus.DRAINING
            service.descriptor.residency = ModelResidency.UNKNOWN
        elif not self.active_leases(lease.service_id) and service.descriptor.status not in {
                ModelServiceStatus.FAILED, ModelServiceStatus.STOPPED}:
            service.descriptor.status = ModelServiceStatus.READY
            service.descriptor.residency = (ModelResidency.RESIDENT if getattr(service, "kind", None)
                                            in {"qwen", "flux", "vlm"} else ModelResidency.UNKNOWN)
            self._idle_since[lease.service_id] = utc_now()
        self._save()
        self._service_event(service, lease.project_id)
        if not uncertain:
            self._emit(EventType.RESOURCE_LEASE_RELEASED, lease.project_id, lease.model_dump(mode="json"), lease.job_id)

    def _record_oom(self, job, lease, snapshot):
        service = self.manager.get(lease.service_id)
        service.descriptor.status = ModelServiceStatus.FAILED
        service.descriptor.residency = ModelResidency.FAILED
        self._resident_credit.pop(lease.service_id, None)
        # Explicit, visible failure backoff. Never silently mutate the configured contract.
        self._oom_penalties[lease.service_id] = self._oom_penalties.get(lease.service_id, 0) + 4 * GiB
        self._decision(job, lease.service_id, snapshot, ["invalidate_lease", "increase_oom_headroom:4GiB"],
                       False, "RESOURCE_EXHAUSTED_OOM; submitted work must not be replayed automatically")

    async def execute(self, job, operation, *, current_job=None, on_ready=None):
        try:
            lease = await self.acquire(job)
        except ProviderFailure as error:
            if isinstance(error,ResourceAdmissionWait):raise
            # Existing job-level transient retry remains owned by LocalJobExecutor.
            # Direct role calls and non-retryable startup failures need workflow admission handling.
            if current_job and error.retryable:raise
            if error.error_type in {ProviderErrorType.UNAVAILABLE,ProviderErrorType.TIMEOUT,ProviderErrorType.MODEL_NOT_READY}:
                # No inference operation has run. Keep uncertain startup leases quarantined;
                # the next admission must reconcile them before any submission.
                raise ResourceAdmissionWait('Admission unavailable: '+error.error_type.value) from error
            raise
        if current_job and current_job().cancellation_requested:
            if lease:
                await self.release(lease)
                for cache in (self._before, self._base_credit, self._isolated, self._startup, self._warmup):
                    cache.pop(lease.lease_id, None)
            return ProviderResult(provider_request_id=job.job_id, success=False,
                                  error_type=ProviderErrorType.CANCELLED, error_message="cancelled before submission")
        if on_ready:
            on_ready()
            job = current_job() if current_job else job
        if lease is None:
            return await operation(job)
        job.resource_lease_id, job.model_service_id = lease.lease_id, lease.service_id
        service = self.manager.get(lease.service_id)
        service.descriptor.status = ModelServiceStatus.BUSY
        service.descriptor.residency = ModelResidency.BUSY
        lease.status = LeaseStatus.EXECUTING
        lease.inference_started = True
        self._save()  # A crash from this point quarantines the lease on restore.
        self._service_event(service, job.project_id)
        before = self._before.pop(lease.lease_id)
        samples = []
        async def monitor():
            while True:
                await asyncio.sleep(self.settings.telemetry_interval)
                try:
                    samples.append(await self.snapshot(job.project_id))
                except Exception as error:
                    self._emit(EventType.RESOURCE_PRESSURE, job.project_id,
                        {"reason": "telemetry unavailable", "error_type": type(error).__name__}, job.job_id)
        task = asyncio.create_task(monitor())
        start = perf_counter()
        try:
            result = await operation(job)
        except BaseException as error:
            result = normalize_media_provider_error(job.job_id, error)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        execution_seconds = perf_counter() - start
        active = current_job() if current_job else job
        after = None
        with suppress(Exception):
            after = await self.snapshot(job.project_id)
        oom = False
        with suppress(Exception):
            oom = await service.oom_killed()
        oom = oom or result.error_type == ProviderErrorType.RESOURCE_EXHAUSTED_OOM
        uncertain = (not result.success and result.metadata.get("request_dispatched") is not False and (result.error_type in {
            ProviderErrorType.REMOTE_COMPLETION_UNCERTAIN, ProviderErrorType.UNAVAILABLE,
            ProviderErrorType.TIMEOUT, ProviderErrorType.INTERNAL, ProviderErrorType.CANCELLED}
            or (active.remote_prompt_id is not None and active.remote_status not in {
                JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED})))
        if oom:
            self._record_oom(job, lease, after or before)
            result = ProviderResult(provider_request_id=result.provider_request_id, success=False,
                error_type=ProviderErrorType.RESOURCE_EXHAUSTED_OOM,
                error_message="media_provider_resource_exhausted_oom", retryable=False)
            uncertain = False  # Docker death is definite termination; no automatic retry.
        elif uncertain:
            active.remote_completion_uncertain = True
            result = result.model_copy(update={"retryable": False,
                "error_type": ProviderErrorType.REMOTE_COMPLETION_UNCERTAIN,
                "error_message": "media_provider_remote_completion_uncertain"})
        release_start = perf_counter()
        await self.release(lease, uncertain=uncertain, invalidated=oom)
        has_execution_sample = bool(samples) or after is not None
        samples = [before, *samples, *([after] if after else [])]
        peak = min(samples, key=lambda x: x.available_unified_memory_bytes)
        if peak.available_unified_memory_bytes < peak.system_reserve_bytes + peak.safety_margin_bytes:
            self._emit(EventType.RESOURCE_PRESSURE, job.project_id,
                {"reason": "sampled memory crossed configured headroom; inspect observed profile before next admission",
                 "snapshot": peak.model_dump(mode="json")}, job.job_id)
        measured = None
        base_credit = self._base_credit.pop(lease.lease_id, 0)
        if (self._isolated.pop(lease.lease_id, False) and not self.active_leases()
                and not uncertain and has_execution_sample):
            measured = max(0, before.available_unified_memory_bytes - peak.available_unified_memory_bytes)
            # This is a sampled *increment*, not a fabricated exact allocator peak.
            measured += base_credit
            if after and result.success:
                retained = max(0, before.available_unified_memory_bytes - after.available_unified_memory_bytes)
                self._resident_credit[lease.service_id] = min(
                    max(self.reservation(lease.service_id), measured or 0),
                    max(self._resident_credit.get(lease.service_id, 0), retained))
        self.observations.append(ResourceObservation(job_id=job.job_id, service_id=lease.service_id,
            resource_before=before, resource_peak=peak if has_execution_sample else None,
            resource_after=after, observed_peak_bytes=measured,
            startup_seconds=self._startup.pop(lease.lease_id, 0), execution_seconds=execution_seconds,
            warmup_seconds=self._warmup.pop(lease.lease_id, 0),
            release_seconds=perf_counter() - release_start,
            outcome=result.error_type.value if result.error_type else "success"))
        self._update_profiles()
        self._audit("observation", self.observations[-1].model_dump(mode="json"))
        self._batch_count = self._batch_count + 1 if self._last_service == lease.service_id else 1
        self._last_service = lease.service_id
        self._save()
        return result

    def _update_profiles(self):
        for service in self.manager.all():
            profile = service.descriptor.runtime_profile
            if profile:
                values = [x.observed_peak_bytes for x in self.observations
                          if x.service_id == profile.service_id and x.observed_peak_bytes is not None][-20:]
                profile.observed_peak_bytes = max(values) if values else None

    async def reconcile(self, lease_id):
        """Only definite container death or a terminal remote history can clear quarantine."""
        async with self._lock:
            lease = self.leases[lease_id]
            service = self.manager.get(lease.service_id)
            status = await service.status()
            definite = status in {ModelServiceStatus.STOPPED, ModelServiceStatus.FAILED}
            if not definite and not lease.inference_started:
                definite = await service.idle()
            if not definite and lease.remote_prompt_id and hasattr(service, "remote_terminal"):
                definite = await service.remote_terminal(lease.remote_prompt_id)
            if definite:
                if status == ModelServiceStatus.FAILED and await service.oom_killed():
                    from movie_agent.domain import GenerationJob
                    recovered = GenerationJob(job_id=lease.job_id, project_id=lease.project_id,
                        task="reconcile", idempotency_key=lease.job_id, resource_class=lease.resource_class)
                    self._record_oom(recovered, lease, await self.snapshot(lease.project_id))
                await self.release(lease, invalidated=status == ModelServiceStatus.FAILED)
            return definite

    async def maintain(self, project_id="resource-runtime"):
        async with self._lock:
            now = utc_now()
            for service in self.manager.all():
                sid = service.descriptor.service_id
                if service.descriptor.status == ModelServiceStatus.READY and not self.active_leases(sid):
                    self._idle_since.setdefault(sid, now)
            affinity = {self.service_for(x.provider_id) for x in self._ready}
            for sid, since in list(self._idle_since.items()):
                if sid not in affinity and (now - since).total_seconds() >= self.settings.warm_idle_ttl:
                    await self._evict(self.manager.get(sid), project_id)

    def view(self):
        return {"enabled": self.settings.enabled,
                "snapshot": self.last_snapshot.model_dump(mode="json") if self.last_snapshot else None,
                "services": [s.descriptor.model_dump(mode="json") for s in self.manager.all()],
                "leases": [x.model_dump(mode="json") for x in self.active_leases()],
                "decisions": [x.model_dump(mode="json") for x in self.decisions[-10:]],
                "observations": [x.model_dump(mode="json") for x in self.observations[-10:]],
                "oom_headroom_bytes": self._oom_penalties}

    def close(self):
        if getattr(self, "ownership", None):
            self.ownership.close()
