"""P4A fake unified-memory environment: no network, Docker or model required."""

import asyncio
from datetime import timedelta
import json

import pytest

from movie_agent.domain import (GenerationJob, JobStatus, ProviderKind, ProviderResult,
    ProviderErrorType, ResourceClass, CheckpointSnapshot, utc_now)
from movie_agent.execution import JobManager, LocalJobExecutor
from movie_agent.execution.events import LocalEventBus
from movie_agent.execution.scheduler import LocalJobScheduler, LogicalResourceScheduler
from movie_agent.execution.checkpoints import LocalCheckpointStore
from movie_agent.media import MediaModality, ModelServiceStatus, ProviderCapabilities, ResourceProfile
from movie_agent.model_services import ModelManager, MockModelService, ModelServiceDescriptor
from movie_agent.model_services.coordinator import RuntimeCoordinator, ResourceAdmissionWait
from movie_agent.model_services.resources import GiB, ModelRuntimeProfile, ResourceSnapshot, ResourceRuntimeSettings, LeaseStatus
from movie_agent.model_services.spark import SparkDockerServiceController, SparkResourceTelemetry, SparkDockerModelService
from movie_agent.providers.base import ProviderFailure


class Memory:
    def __init__(self, available=112):
        self.available = available * GiB

    async def snapshot(self):
        return ResourceSnapshot(total_unified_memory_bytes=128 * GiB,
            available_unified_memory_bytes=self.available,
            diagnostics={"cuda_total": 128 * GiB, "cuda_free": 128 * GiB})


@pytest.mark.asyncio
async def test_startup_exit_fails_without_waiting_for_health_timeout():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    state={'Running':False,'ExitCode':1,'OOMKilled':False}
    controller=SimpleNamespace(action=AsyncMock(),inspect=AsyncMock(return_value=state),
        is_controlled_stop=lambda *_:False)
    service=SparkDockerModelService(Service('music',Memory()).descriptor,controller,
        health_path='/health',idle_path='/v1/stats',kind='music')
    service.health=AsyncMock(return_value=False)
    manager=ModelManager();manager.register(service)
    with pytest.raises(ProviderFailure,match='exited before readiness'):
        await asyncio.wait_for(manager.ensure_ready('music'),timeout=.5)
    assert service.descriptor.status==ModelServiceStatus.FAILED


class Service(MockModelService):
    def __init__(self, sid, memory, peak=40, resident=24):
        super().__init__(ModelServiceDescriptor(service_id=sid, modality=MediaModality.VIDEO,
            endpoint="mock://service", resource_profile=ResourceProfile(resource_class=ResourceClass.HEAVY),
            capabilities=ProviderCapabilities(provider_id=sid, kind=ProviderKind.VIDEO, modalities=[MediaModality.VIDEO]),
            runtime_profile=ModelRuntimeProfile(service_id=sid, provider_id=sid,
                model_profile_id=sid, estimated_resident_bytes=resident * GiB,
                estimated_peak_bytes=peak * GiB, estimate_basis="fake", startup_cost_seconds=10)))
        self.memory, self.resident = memory, resident * GiB
        self.starts = self.stops = 0
        self.oom = False
        self.busy_external = False
        self.bad_health = False
        self.delay = 0

    async def start(self):
        await asyncio.sleep(self.delay)
        self.starts += 1
        self.memory.available -= self.resident
        return await super().start()

    async def stop(self):
        self.stops += 1
        self.memory.available += self.resident
        return await super().stop()

    async def health(self):
        return not self.bad_health and await super().health()

    async def idle(self):
        return not self.busy_external and await super().idle()

    async def oom_killed(self):
        return self.oom


def environment(tmp_path=None, available=112):
    memory = Memory(available)
    manager = ModelManager()
    for sid, peak, resident in [("qwen", 40, 24), ("flux", 44, 32), ("h3", 88, 48)]:
        manager.register(Service(sid, memory, peak, resident))
    runtime = RuntimeCoordinator(manager, memory,
        settings=ResourceRuntimeSettings(system_reserve_gib=8, service_start_timeout=0.2, telemetry_interval=1),
        state_path=tmp_path / "state.json" if tmp_path else None)
    return runtime, memory


def job(sid="h3", **kwargs):
    return GenerationJob(project_id="test", job_id=kwargs.pop("job_id", sid), task="video",
        provider_id=sid, resource_class=ResourceClass.HEAVY, idempotency_key=kwargs.pop("key", sid), **kwargs)


async def success(active):
    return ProviderResult(provider_request_id=active.job_id, success=True)


@pytest.mark.asyncio
async def test_admit_lease_acquired_before_inference_and_released(tmp_path):
    runtime, _ = environment(tmp_path)
    async def operation(active):
        assert len(runtime.active_leases()) == 1
        assert active.resource_lease_id
        assert runtime.active_leases()[0].status == LeaseStatus.EXECUTING
        return await success(active)
    assert (await runtime.execute(job(), operation)).success
    assert not runtime.active_leases()
    assert runtime.manager.get("h3").starts == 1
    assert runtime.observations[0].resource_after is not None


@pytest.mark.asyncio
async def test_insufficient_memory_waits_without_provider_call():
    runtime, _ = environment(available=80)
    jobs = JobManager(LocalEventBus(), "trace")
    jobs.runtime_coordinator = runtime
    jobs.add(job())
    async def forbidden(_):
        pytest.fail("Provider must not be invoked without admission")
    result = await LocalJobExecutor(jobs).execute("h3", forbidden)
    assert result.status == JobStatus.WAITING_RESOURCE
    assert not runtime.active_leases()
    assert runtime.manager.get("h3").starts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("sid", ["qwen", "flux"])
async def test_idle_service_evicted_for_h3(sid):
    runtime, memory = environment()
    idle = runtime.manager.get(sid)
    await idle.start()
    assert (await runtime.execute(job(), success)).success
    assert idle.stops == 1
    assert any(f"drain/stop:{sid}" in d.actions for d in runtime.decisions)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [ModelServiceStatus.BUSY, ModelServiceStatus.DRAINING])
async def test_busy_or_draining_cannot_evict(state):
    runtime, _ = environment()
    service = runtime.manager.get("qwen")
    await service.start()
    service.descriptor.status = state
    with pytest.raises(ResourceAdmissionWait):
        await runtime.acquire(job())
    assert service.stops == 0


@pytest.mark.asyncio
async def test_external_busy_or_unknown_blocks_eviction():
    runtime, _ = environment()
    service = runtime.manager.get("flux")
    await service.start()
    service.busy_external = True
    with pytest.raises(ResourceAdmissionWait):
        await runtime.acquire(job())
    assert service.stops == 0


@pytest.mark.asyncio
async def test_uncertain_lease_survives_restart_blocks_eviction_and_duplicate(tmp_path):
    runtime, memory = environment(tmp_path)
    async def uncertain(active):
        runtime.mark_submitted(active, "remote-123")
        raise ProviderFailure("disconnected", ProviderErrorType.REMOTE_COMPLETION_UNCERTAIN)
    result = await runtime.execute(job("qwen"), uncertain)
    assert result.retryable is False
    assert runtime.active_leases()[0].status == LeaseStatus.UNCERTAIN
    restarted = RuntimeCoordinator(runtime.manager, memory, state_path=tmp_path / "state.json")
    assert restarted.active_leases()[0].remote_prompt_id == "remote-123"
    with pytest.raises(ResourceAdmissionWait):
        await restarted.acquire(job("qwen"))
    with pytest.raises(ResourceAdmissionWait):
        await restarted.acquire(job())
    assert runtime.manager.get("qwen").stops == 0


@pytest.mark.asyncio
async def test_active_lease_counts_and_concurrent_admission():
    runtime, _ = environment()
    lease = await runtime.acquire(job("flux"))
    with pytest.raises(ResourceAdmissionWait):
        await runtime.acquire(job("flux", job_id="another", key="another"))
    assert len(runtime.active_leases()) == 1
    assert runtime.manager.get("flux").stops == 0
    await runtime.release(lease)
    assert not runtime.active_leases()


@pytest.mark.asyncio
async def test_safety_margin_and_unified_memory_not_added_twice():
    runtime, _ = environment(available=100)
    snapshot = await runtime.snapshot()
    assert snapshot.diagnostics["cuda_free"] == 128 * GiB
    assert runtime.can_admit("h3", snapshot)[0] is False  # 88 + 8 + 8 > 100
    runtime.settings.safety_margin_gib = 0
    assert runtime.can_admit("h3", await runtime.snapshot())[0] is True


@pytest.mark.asyncio
async def test_warm_service_avoids_reload_and_resident_double_count():
    runtime, _ = environment()
    assert (await runtime.execute(job("flux"), success)).success
    assert (await runtime.execute(job("flux", job_id="second", key="second"), success)).success
    assert runtime.manager.get("flux").starts == 1
    assert runtime.manager.get("flux").stops == 0


@pytest.mark.asyncio
async def test_priority_dag_and_affinity_batching():
    runtime, _ = environment()
    await runtime.manager.get("flux").start()
    jobs = JobManager(LocalEventBus(), "trace")
    jobs.runtime_coordinator = runtime
    scheduler = LocalJobScheduler(jobs, LogicalResourceScheduler())
    a = job("flux", job_id="frame1", key="1")
    b = job("h3", job_id="video", key="2", dependencies=["frame1"])
    c = job("flux", job_id="frame2", key="3")
    calls = []
    async def operation(active):
        calls.append(active.job_id)
        return await success(active)
    await scheduler.run([b, a, c], operation)
    assert calls == ["frame1", "frame2", "video"]
    high = job("h3", priority=99)
    assert runtime.order_ready([a, high])[0] == high


def test_affinity_fairness_and_human_gate_eligibility():
    runtime, _ = environment()
    runtime.manager.get("flux").descriptor.status = ModelServiceStatus.READY
    old = job("h3", created_at=utc_now() - timedelta(minutes=1))
    warm = job("flux")
    assert runtime.order_ready([old, warm])[0] == warm
    runtime._batch_count = runtime.settings.max_affinity_batch
    assert runtime.order_ready([old, warm])[0] == old


@pytest.mark.asyncio
async def test_gate_and_unsatisfied_dependency_never_execute():
    runtime, _ = environment()
    jobs = JobManager(LocalEventBus(), "trace")
    jobs.runtime_coordinator = runtime
    gate = job("flux", status=JobStatus.WAITING_HUMAN)
    dependent = job("h3", dependencies=[gate.job_id])
    async def forbidden(_):
        pytest.fail("gate/dependency must outrank all affinities")
    await LocalJobScheduler(jobs, LogicalResourceScheduler()).run([gate, dependent], forbidden)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["health", "timeout"])
async def test_health_failure_and_start_timeout_cleanup(failure):
    runtime, _ = environment()
    service = runtime.manager.get("h3")
    service.bad_health = failure == "health"
    service.delay = 1 if failure == "timeout" else 0
    with pytest.raises(ProviderFailure):
        await runtime.acquire(job())
    assert not runtime.active_leases()
    assert service.descriptor.status == ModelServiceStatus.FAILED


@pytest.mark.asyncio
async def test_oom_normalized_and_no_automatic_retry_and_future_headroom(tmp_path):
    runtime, _ = environment(tmp_path)
    jobs = JobManager(LocalEventBus(), "trace")
    jobs.runtime_coordinator = runtime
    jobs.add(job(retry_budget=2))
    calls = []
    async def oom(active):
        calls.append(active.job_id)
        runtime.manager.get("h3").oom = True
        return ProviderResult(provider_request_id=active.job_id, success=False,
            error_type=ProviderErrorType.UNAVAILABLE, retryable=True)
    failed = await LocalJobExecutor(jobs).execute("h3", oom)
    assert failed.failure_reason == "media_provider_resource_exhausted_oom"
    assert len(calls) == 1
    assert not runtime.active_leases()
    assert runtime.manager.get("h3").descriptor.status == ModelServiceStatus.FAILED
    assert runtime.reservation("h3") >= 92 * GiB


@pytest.mark.parametrize("state, expected", [
    ({"Running": False, "ExitCode": 137}, True),
    ({"Running": False, "ExitCode": 1, "OOMKilled": True}, True),
    ({"Running": False, "ExitCode": 0, "OOMKilled": False}, False),
    ({"Running": True, "ExitCode": 0}, False)])
def test_docker_exit137_and_oom_flag(state, expected):
    assert SparkDockerModelService._oom(state) is expected


def test_verified_controlled_sigkill_is_not_a_new_oom():
    state = {"Running": False, "ExitCode": 137, "OOMKilled": False, "FinishedAt": "time1"}
    controller = SparkDockerServiceController(ResourceRuntimeSettings(), runner=lambda *args: None)
    assert not controller.is_controlled_stop("comfyui", state)
    assert SparkDockerModelService._oom(state)
    controller.record_controlled_stop("comfyui", state)
    assert controller.is_controlled_stop("comfyui", state)
    assert not SparkDockerModelService._oom(state, controlled_stop=True)
    assert not controller.is_controlled_stop("comfyui", {**state, "FinishedAt": "time2"})
    assert SparkDockerModelService._oom({**state, "OOMKilled": True}, controlled_stop=True)


@pytest.mark.asyncio
async def test_completed_job_resume_with_services_stopped_does_not_rerun(tmp_path):
    runtime, _ = environment()
    snapshot = CheckpointSnapshot(project_id="test", workflow_graph={}, project_state={},
        active_jobs=[job(status=JobStatus.SUCCEEDED), job("flux", status=JobStatus.RUNNING,
            remote_prompt_id="submitted", remote_completion_uncertain=True)])
    store = LocalCheckpointStore(tmp_path)
    resumed = store.prepare_resume(store.save(snapshot))
    assert resumed.active_jobs[1].status == JobStatus.RUNNING
    jobs = JobManager(LocalEventBus(), "trace")
    jobs.runtime_coordinator = runtime
    jobs.add(resumed.active_jobs[0])
    async def forbidden(_):
        pytest.fail("committed jobs must be reused")
    assert (await LocalJobExecutor(jobs).execute("h3", forbidden)).status == JobStatus.SUCCEEDED
    assert runtime.manager.get("h3").starts == 0
    assert (await runtime.execute(job("qwen"), success)).success
    assert runtime.manager.get("qwen").starts == 1


@pytest.mark.asyncio
async def test_observation_high_water_history_and_ttl():
    runtime, memory = environment()
    async def grow(active):
        memory.available -= 5 * GiB
        return await success(active)
    await runtime.execute(job("flux"), grow)
    observation = runtime.observations[0]
    assert observation.observed_peak_bytes == 37 * GiB
    assert runtime.manager.get("flux").descriptor.runtime_profile.observed_peak_bytes == 37 * GiB
    runtime.settings.warm_idle_ttl = 0
    await runtime.maintain("test")
    assert runtime.manager.get("flux").stops == 1
    assert len(runtime.observations) == 1


@pytest.mark.asyncio
async def test_controller_allowlist_idempotence_and_telemetry():
    commands = []
    running = False
    async def run(command, timeout):
        nonlocal running
        commands.append(command)
        if command == "cat /proc/meminfo":
            return "MemTotal: 134217728 kB\nMemAvailable: 104857600 kB\n"
        if command.startswith("docker inspect"):
            return json.dumps({"Running": running})
        running = command.startswith("docker start")
        return ""
    controller = SparkDockerServiceController(ResourceRuntimeSettings(), runner=run)
    await controller.action("qwen", "start")
    await controller.action("qwen", "start")
    assert commands.count("docker start movie-agent-llm") == 1
    with pytest.raises(ValueError):
        await controller.action("qwen; reboot", "stop")
    with pytest.raises(ValueError):
        await controller.action("qwen", "rm")
    snapshot = await SparkResourceTelemetry(controller).snapshot()
    assert snapshot.available_unified_memory_bytes == 100 * GiB


@pytest.mark.asyncio
async def test_mock_jobs_use_uniform_interface_without_spark():
    runtime, _ = environment()
    assert (await runtime.execute(job("mock-video"), success)).success
    assert not runtime.leases


@pytest.mark.asyncio
async def test_continuity_lock_spans_providers_and_direct_executor_checks_dependencies():
    runtime, _ = environment()
    lease = await runtime.acquire(job("qwen", continuity_chain_id="chain"))
    with pytest.raises(ResourceAdmissionWait, match="continuity"):
        await runtime.acquire(job("flux", continuity_chain_id="chain"))
    await runtime.release(lease)
    jobs = JobManager(LocalEventBus(), "trace")
    jobs.runtime_coordinator = runtime
    jobs.add(job("flux", dependencies=["not-committed"]))
    async def forbidden(_):
        pytest.fail("direct execution cannot bypass dependency readiness")
    blocked = await LocalJobExecutor(jobs).execute("flux", forbidden)
    assert blocked.status == JobStatus.BLOCKED


@pytest.mark.asyncio
async def test_cancellation_during_start_never_submits():
    runtime, _ = environment()
    jobs = JobManager(LocalEventBus(), "trace")
    jobs.runtime_coordinator = runtime
    jobs.add(job())
    runtime.manager.get("h3").delay = 0.05
    async def forbidden(_):
        pytest.fail("cancelled startup must not submit remote inference")
    task = asyncio.create_task(LocalJobExecutor(jobs).execute("h3", forbidden))
    await asyncio.sleep(0.01)
    jobs.request_cancel("h3")
    result = await task
    assert result.status == JobStatus.CANCELLED
    assert not runtime.active_leases()


def test_runtime_owner_rejects_second_process_owner(tmp_path):
    from movie_agent.model_services.ownership import RuntimeOwnership
    first = RuntimeOwnership(tmp_path / "owner")
    try:
        with pytest.raises(RuntimeError, match="Another production worker"):
            RuntimeOwnership(tmp_path / "owner")
    finally:
        first.close()
    RuntimeOwnership(tmp_path / "owner").close()


@pytest.mark.asyncio
async def test_resume_preserves_uncertain_until_remote_terminal(tmp_path):
    runtime, _ = environment(tmp_path)
    unknown = job("qwen", status=JobStatus.FAILED, remote_completion_uncertain=True)
    runtime.restore_jobs([unknown])
    lease = runtime.active_leases()[0]
    runtime.manager.get("qwen").descriptor.status = ModelServiceStatus.DRAINING
    assert not await runtime.reconcile(lease.lease_id)
    runtime.manager.get("qwen").descriptor.status = ModelServiceStatus.STOPPED
    assert await runtime.reconcile(lease.lease_id)
    assert not runtime.active_leases()


@pytest.mark.asyncio
async def test_real_measured_h3_budget_and_warm_admission():
    runtime, _ = environment()
    runtime.settings.system_reserve_gib = 4
    h3 = runtime.manager.get("h3")
    h3.descriptor.runtime_profile.estimated_peak_bytes = 104 * GiB
    cold = ResourceSnapshot(total_unified_memory_bytes=int(121.70 * GiB),
        available_unified_memory_bytes=int(117.29 * GiB), system_reserve_bytes=4 * GiB, safety_margin_bytes=8 * GiB)
    assert runtime.can_admit("h3", cold)[0]
    runtime._resident_credit["h3"] = int(103.73 * GiB)
    warm = cold.model_copy(update={"available_unified_memory_bytes": int(13.56 * GiB)})
    assert runtime.can_admit("h3", warm)[0]
    runtime._oom_penalties["h3"] = 4 * GiB
    assert not runtime.can_admit("h3", warm)[0]
