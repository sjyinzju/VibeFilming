"""Composition service for provider routing, jobs, binary storage, and provenance."""

from __future__ import annotations

from collections.abc import Callable

from movie_agent.artifacts import ArtifactStore
from movie_agent.domain import (
    Artifact,
    ArtifactType,
    EventEnvelope,
    EventType,
    GenerationJob,
    GenerationStrategyType,
    JobStatus,
    ProviderErrorType,
    ProviderResult,
    Provenance,
)
from movie_agent.execution import JobManager, LocalJobExecutor
from movie_agent.execution.events import EventBus
from movie_agent.media.contracts import (
    AudioGenerationResult,
    AudioRequestBase,
    ImageGenerationRequest,
    ImageGenerationResult,
    MediaArtifactMetadata,
    MediaDuration,
    MediaEncoding,
    MediaModality,
    MediaRoutingRequest,
    MediaGenerationStrategy,
    PostProductionRequest,
    PostProductionResult,
    VideoGenerationRequest,
    VideoGenerationResult,
    VisionInspectionRequest,
    VisionInspectionResult,
)
from movie_agent.media.storage import LocalBinaryArtifactStore
from movie_agent.providers.media import (
    AudioProvider,
    BinaryPayload,
    ImageProvider,
    PostProcessor,
    ProviderMediaResponse,
    VideoProvider,
    MediaProviderProgress,
    VisionProvider,
    _png,
    normalize_media_provider_error,
)
from movie_agent.providers.base import ProviderFailure
from movie_agent.providers.registry import MediaRouter, ProviderRegistry


class MediaRuntime:
    """The stable workflow-facing path used by both mock and future real providers."""

    def __init__(
        self,
        artifact_store: ArtifactStore,
        binary_store: LocalBinaryArtifactStore,
        providers: ProviderRegistry,
        event_bus: EventBus,
        trace_id: str,
        runtime_coordinator=None,
    ) -> None:
        self.artifact_store = artifact_store
        self.binary_store = binary_store
        self.providers = providers
        self.router = MediaRouter(providers)
        self.runtime_coordinator = runtime_coordinator
        self.router.runtime_coordinator = runtime_coordinator
        self.event_bus = event_bus
        self.trace_id = trace_id
        self.job_manager: JobManager | None = None
        self.inspections: list[VisionInspectionResult] = []
        self._request_routes: dict[str, tuple[str, str]] = {}
        self.checkpoint_callback = None
        self.recover_inspections()

    def recover_inspections(self):
        """Production evidence wins over an older checkpoint after a crash."""
        known = {i.result_id: i for i in self.inspections}
        for artifact in self.artifact_store.list_all():
            if artifact.metadata.get("purpose") == "vision_inspection":
                result = VisionInspectionResult.model_validate(self.artifact_store.read_structured(artifact))
                known[result.result_id] = result
        self.inspections = list(known.values())

    def bind_jobs(self, manager: JobManager) -> None:
        self.job_manager = manager
        manager.runtime_coordinator = self.runtime_coordinator

    async def capabilities(self):
        await self.router.refresh()
        return self.router.capabilities.all()

    def reserve_quality_work(self, kind, subject, request):
        ledger = getattr(self, 'quality_ledger', None)
        if ledger is None:
            return
        from movie_agent.quality.budget import BudgetExhausted
        payload = request.model_dump(mode='json')
        def stable(value):
            if isinstance(value, dict):
                return {k:stable(v) for k,v in value.items() if k not in {
                    'request_id','job_id','reference_id','prompt_package_id','created_at'}}
            return [stable(v) for v in value] if isinstance(value,list) else value
        key, fresh = ledger.reserve(kind, subject, inputs=stable(payload),
                                    config={'providers':sorted(p.provider_id for p in self.providers.all())})
        if not fresh:
            raise BudgetExhausted('Dispatch already reserved without a recovered output; human review required: '+key)

    async def generate_image(
        self,
        job: GenerationJob,
        request: ImageGenerationRequest,
        *,
        artifact_type: ArtifactType = ArtifactType.IMAGE,
    ) -> Artifact:
        configured = next((item for item in self.providers.all() if isinstance(item, ImageProvider)), None)
        initial_provider_id = configured.provider_id if configured else "unrouted-image"
        response: ProviderMediaResponse[ImageGenerationResult] | None = None
        selection = None
        routing_error = None
        try:
            selection = await self.router.select(MediaRoutingRequest(
                modality=MediaModality.IMAGE, task="frame" if artifact_type == ArtifactType.FRAME else "image",
                required_capabilities=[item.capability for item in request.required_capabilities if item.required],
                quality_profile=request.quality_profile, resource_class=request.resource_class,
            ))
            initial_provider_id = selection.provider_id
        except Exception as error:
            routing_error = error

        async def operation(active: GenerationJob) -> ProviderResult:
            nonlocal response
            try:
                if routing_error:
                    raise routing_error
                provider = self.providers.get(selection.provider_id)
                if not isinstance(provider, ImageProvider):
                    raise TypeError("selected provider does not implement ImageProvider")
                self._request_routes[active.job_id] = (provider.provider_id, request.request_id)
                active = active.model_copy(update={"provider_id": provider.provider_id})
                self.job_manager._jobs[active.job_id] = active
                if request.mode.value == 'image_edit':
                    self.reserve_quality_work('frame_edit', request.output_artifact_id, request)
                elif getattr(self,'inspection_context',None):
                    self.reserve_quality_work('frame_regenerate', request.output_artifact_id, request)
                response = await provider.generate(request)
                if self.job_manager.get(active.job_id).cancellation_requested:
                    from movie_agent.providers.base import ProviderFailure
                    raise ProviderFailure("Image job cancelled before artifact registration", ProviderErrorType.CANCELLED)
                artifact = self._register_response(
                    job=active, response=response, modality=MediaModality.IMAGE,
                    artifact_type=artifact_type, purpose=request.purpose.value,
                    input_ids=[item.artifact_id for item in request.references]
                              + ([request.source_image.artifact_id] if request.source_image else []),
                    prompt=request.prompt_package, seed=response.result.seed,
                    dimensions=response.result.dimensions,
                    encoding=response.result.encoding,
                )
                return ProviderResult(provider_request_id=request.request_id, success=True,
                                      artifact_ids=[artifact.artifact_id],
                                      metadata=response.result.provider_metadata)
            except BaseException as error:
                return normalize_media_provider_error(request.request_id, error)

        await self._execute(job, initial_provider_id, request.request_id, operation, preflight_error=routing_error)
        return self._required(request.output_artifact_id,job_id=job.job_id)

    async def generate_video(self, job: GenerationJob, request: VideoGenerationRequest) -> Artifact:
        from movie_agent.media.video_conditioning import validate_reviewed_boundaries
        validate_reviewed_boundaries(request,self.artifact_store)
        if request.reference_conditioning:
            job=job.model_copy(update={'provenance':job.provenance.model_copy(update={'parameters':{
                **job.provenance.parameters,'reference_conditioning':request.reference_conditioning.model_dump(mode='json')}})})
        required_by_mode = {
            "image_to_video": ["image_to_video"],
            "first_frame_to_video": ["first_frame"],
            "first_last_frame_to_video": ["first_frame", "last_frame", "first_last_frame"],
            "reference_to_video": ["multi_reference"],
            "video_to_video": ["video_to_video"],
            "video_extend": ["video_extend"],
        }
        routing_capabilities = list(dict.fromkeys([
            *required_by_mode.get(request.mode.value, []),
            *[item.capability for item in request.required_capabilities if item.required],
        ]))
        selection = await self.router.select(MediaRoutingRequest(
            modality=MediaModality.VIDEO, task="video",
            required_capabilities=routing_capabilities,
            quality_profile=request.quality_profile, resource_class=request.resource_class,
        ))
        provider = self.providers.get(selection.provider_id)
        if not isinstance(provider, VideoProvider):
            raise TypeError("selected provider does not implement VideoProvider")

        strategy = MediaGenerationStrategy(
            strategy_type=GenerationStrategyType(job.strategy_type or request.mode.value),
            reason="Persisted GenerationStrategy selected before provider routing.",
            required_capabilities=list(dict.fromkeys(routing_capabilities)),
            input_artifact_ids=list(job.input_artifact_ids),
        )
        preflight = await provider.preflight(request, strategy=strategy)
        validate_reviewed_boundaries(preflight.effective_request,self.artifact_store)
        effective_request = preflight.effective_request
        preflight_parameters = {
            "requested_delivery_dimensions": preflight.requested_delivery_dimensions.model_dump(mode="json"),
            "effective_generation_dimensions": preflight.effective_generation_dimensions.model_dump(mode="json"),
            "adaptation_reason": list(preflight.adaptation_reason),
            "video_preflight_initial_issues": [
                item.model_dump(mode="json") for item in preflight.initial_issues
            ],
            "video_preflight_remaining_issues": [
                item.model_dump(mode="json") for item in preflight.remaining_issues
            ],
            "video_preflight_input_artifacts": list(preflight.input_artifacts),
        }
        job = job.model_copy(update={
            "provenance": job.provenance.model_copy(update={
                "seed": effective_request.seed,
                "parameters": {**job.provenance.parameters, **preflight_parameters},
            }, deep=True),
        }, deep=True)

        async def operation(active: GenerationJob) -> ProviderResult:
            try:
                if not preflight.compatible:
                    details = "; ".join(
                        f"{item.code}:{item.field_path}" for item in preflight.remaining_issues
                    )
                    raise ProviderFailure(
                        f"Video generation preflight failed: {details}",
                        ProviderErrorType.UNSUPPORTED_CAPABILITY,
                    )

                async def progress(update: MediaProviderProgress) -> None:
                    self.job_manager.provider_activity(
                        active.job_id,
                        remote_status=update.status,
                        activity=update.activity,
                        progress=update.progress,
                        progress_is_determinate=update.progress_is_determinate,
                        remote_event=update.remote_event,
                        provider_execution_graph=update.provider_execution_graph,
                        provider_execution_update=update.provider_execution_update,
                    )

                self.reserve_quality_work('video_regenerate', effective_request.shot_id, effective_request)
                response = await provider.generate(
                    effective_request, strategy=strategy, on_progress=progress
                )
                input_ids = list(dict.fromkeys([
                    *[item.artifact_id for item in effective_request.references],
                    *[item.artifact_id for item in (effective_request.reference_conditioning.reference_assets
                        if effective_request.reference_conditioning else [])],
                    *[item.artifact_id for item in (
                        effective_request.first_frame,
                        effective_request.last_frame,
                        effective_request.previous_shot,
                    ) if item is not None],
                ]))
                artifact = self._register_response(
                    job=active, response=response, modality=MediaModality.VIDEO,
                    artifact_type=ArtifactType.VIDEO, purpose="shot_video",
                    input_ids=input_ids,
                    prompt=effective_request.prompt_package, seed=effective_request.seed,
                    dimensions=response.result.dimensions,
                    duration=response.result.duration_seconds,
                    encoding=response.result.encoding,
                    extra={"fps": effective_request.fps, "frame_count": response.result.frame_count,
                           "codec": response.result.codec},
                )
                related = [artifact]
                for audio in response.result.native_audio_outputs:
                    related.append(self._register_native_video_audio(
                        job=active,
                        response=response,
                        output=audio,
                        input_ids=input_ids,
                        prompt=effective_request.prompt_package,
                        seed=effective_request.seed,
                    ))
                registered_ids = {item.artifact_id for item in related}
                for payload in response.payloads:
                    if payload.artifact_id not in registered_ids:
                        related.append(self._register_related_video_payload(
                            job=active,
                            response=response,
                            payload=payload,
                            input_ids=input_ids,
                            prompt=effective_request.prompt_package,
                            seed=effective_request.seed,
                        ))
                        registered_ids.add(payload.artifact_id)
                return ProviderResult(provider_request_id=request.request_id, success=True,
                                      artifact_ids=[item.artifact_id for item in related],
                                      metadata=response.result.provider_metadata)
            except BaseException as error:
                return normalize_media_provider_error(request.request_id, error)

        await self._execute(job, selection.provider_id, effective_request.request_id, operation,
            preflight_error=None if preflight.compatible else ProviderFailure(
                "Video generation preflight failed", ProviderErrorType.UNSUPPORTED_CAPABILITY))
        return self._required(request.output_artifact_id,job_id=job.job_id)

    async def inspect(self, job: GenerationJob, request: VisionInspectionRequest) -> VisionInspectionResult:
        from movie_agent.media.inspection import pin_inspection
        from movie_agent.quality.vision import (inspection_fingerprint, draft_from_result,
            VisionDecisionPolicy, critic_configuration_fingerprint, supported_repair_actions)
        context=getattr(self,'inspection_context',None)
        if context:
            request=request.model_copy(update={k:v for k,v in context.items() if not getattr(request,k,None)})
        request = pin_inspection(request, self.artifact_store, self.binary_store)
        selection = await self.router.select(MediaRoutingRequest(
            modality=MediaModality.VISION, task="inspect",
            required_capabilities=["video" if request.video_artifact_id else "image"],
            resource_class=job.resource_class,
        ))
        provider = self.providers.get(selection.provider_id)
        if not isinstance(provider, VisionProvider):
            raise TypeError("selected provider does not implement VisionProvider")
        supported_actions = supported_repair_actions(self.router.capabilities.all())
        request = request.model_copy(update={"critic_configuration_fingerprint": critic_configuration_fingerprint(provider, supported_actions)})
        request = await provider.prepare_request(request)
        fingerprint = inspection_fingerprint(request)
        identity = request.shot_id or request.video_artifact_id or request.image_artifact_id
        core_job_id = f"inspect:{identity}:v{request.target_artifact_version}:{fingerprint[:16]}"
        job = job.model_copy(update={"job_id": core_job_id, "idempotency_key": core_job_id})
        request = request.model_copy(update={"job_id": core_job_id})
        self.recover_inspections()
        cached = next((i for i in self.inspections if i.inspection_fingerprint == fingerprint
                       and i.provider_id == selection.provider_id), None)
        if cached:
            # The immutable, validated result proves this read-only request ended.
            ledger=getattr(self,'quality_ledger',None)
            if ledger and request.quality_policy_revision:
                reservation=next((r for r in ledger.critic_records() if r['key']==fingerprint),None)
                if reservation:
                    ledger.finish_critic(reservation,result=cached)
            if self.runtime_coordinator:
                for lease in list(self.runtime_coordinator.active_leases()):
                    if lease.job_id == job.job_id and lease.project_id == job.project_id:
                        await self.runtime_coordinator.release(lease)
            if self.job_manager:
                original = self.job_manager._jobs.get(job.job_id, job)
                recovered = original.model_copy(update={"status": JobStatus.SUCCEEDED,
                    "provider_id": cached.provider_id, "activity": "reused committed inspection"})
                self.job_manager._jobs[job.job_id] = recovered
                self.job_manager._idempotency[job.idempotency_key] = job.job_id
            return cached
        ledger=getattr(self,'quality_ledger',None)
        if ledger:
            ledger.ensure_inspection_available(request.video_artifact_id or request.image_artifact_id,request)
        result: VisionInspectionResult | None = None
        async def operation(active: GenerationJob) -> ProviderResult:
            nonlocal result
            critic_reservation=None
            try:
                self._emit(EventType.MEDIA_EVALUATION_STARTED, job.project_id,
                    {"request_id": request.request_id, "target_artifact_id": request.video_artifact_id or request.image_artifact_id,
                     "target_artifact_version": request.target_artifact_version, "provider_id": provider.provider_id,
                     "status": "evaluating"}, job=job)
                if ledger and request.quality_policy_revision:
                    critic_reservation=ledger.reserve_critic(request,fingerprint)
                else:
                    self.reserve_quality_work('vlm_inspection', request.video_artifact_id or request.image_artifact_id, request)
                proposal = await provider.inspect(request)
                target = request.video_artifact_id or request.image_artifact_id
                if (proposal.request_id != request.request_id or proposal.target_artifact_id != target
                    or proposal.target_artifact_version not in {None, request.target_artifact_version}
                    or proposal.target_sha256 not in {None, request.target_sha256}):
                    raise ValueError("provider returned an inspection for a different immutable target")
                from movie_agent.quality.vision import effective_vision_policy
                policy = effective_vision_policy(request,getattr(provider, "policy", VisionDecisionPolicy()))
                result = policy.commit(request, draft_from_result(proposal), provider_id=selection.provider_id,
                    model=proposal.provider_metadata.get("model"), metadata=proposal.provider_metadata,
                    supported_actions=supported_actions)
                artifact = self.artifact_store.create_structured(request.output_artifact_id,
                    result.model_dump(mode="json"), source_job_id=active.job_id,
                    parent_artifact_ids=result.provenance.input_artifact_ids,
                    provenance=result.provenance, metadata={"purpose": "vision_inspection",
                        "inspection_fingerprint": fingerprint, "target_artifact_version": request.target_artifact_version,
                        "target_sha256": request.target_sha256})
                self.inspections.append(result)
                if critic_reservation:
                    ledger.finish_critic(critic_reservation,result=result)
                self._artifact_event(artifact, job)
                if self.checkpoint_callback:
                    self.checkpoint_callback()
                return ProviderResult(provider_request_id=request.request_id, success=True,
                    artifact_ids=[artifact.artifact_id], metadata={"inspection_result_id": result.result_id,
                        "attempts": result.provider_metadata.get("attempts", [])})
            except BaseException as error:
                if critic_reservation:
                    ledger.finish_critic(critic_reservation,error=error)
                return normalize_media_provider_error(request.request_id, error)

        await self._execute(job, selection.provider_id, request.request_id, operation)
        if result is None:
            raise RuntimeError("vision provider completed without a result")
        self._emit(EventType.MEDIA_EVALUATION_COMPLETED, job.project_id,
                   {"result_id": result.result_id, "target_artifact_id": result.target_artifact_id,
                    "target_artifact_version": result.target_artifact_version,
                    "decision": result.decision.value, "provider_id": result.provider_id}, job=job)
        if self.checkpoint_callback:
            self.checkpoint_callback()
        return result

    async def generate_audio(self, job: GenerationJob, request: AudioRequestBase) -> Artifact:
        from movie_agent.media.post import fingerprint, stream_hash
        content = request.model_dump(mode='json', exclude={'request_id', 'job_id', 'created_at', 'output_artifact_id', 'prompt_package'})
        for ref in content.get('references', []):
            ref.pop('reference_id', None)
            for field in ('semantic_role','human_acceptance_artifact_id','accepted_limitations'):
                if not ref.get(field):ref.pop(field,None)
        if content.get('reference_voice'):
            content['reference_voice'].pop('reference_id', None)
            for field in ('semantic_role','human_acceptance_artifact_id','accepted_limitations'):
                if not content['reference_voice'].get(field):content['reference_voice'].pop(field,None)
        content['prompt'] = request.prompt_package.positive_prompt
        content['provider_bindings'] = sorted(p.provider_id for p in self.providers.all() if isinstance(p, AudioProvider))
        content['models'] = [getattr(getattr(p, 'settings', None), name, None)
            for p in self.providers.all() if isinstance(p, AudioProvider) for name in ('tts_model', 'music_model')]
        key = fingerprint(content)
        for saved in self.artifact_store.list_versions(request.output_artifact_id):
            if saved.metadata.get('audio_request_fingerprint') == key:
                with self.binary_store.open(saved.uri) as stream:
                    if stream_hash(stream) != saved.metadata['sha256']:
                        raise ValueError('Committed audio hash mismatch')
                return saved
        selection = await self.router.select(MediaRoutingRequest(
            modality=MediaModality.AUDIO, task=request.purpose.value,
            required_capabilities=[item.capability for item in request.required_capabilities if item.required],
            quality_profile=request.quality_profile, resource_class=request.resource_class,
        ))
        provider = self.providers.get(selection.provider_id)
        if not isinstance(provider, AudioProvider):
            raise TypeError("selected provider does not implement AudioProvider")

        async def operation(active: GenerationJob) -> ProviderResult:
            try:
                self.reserve_quality_work('music' if request.purpose.value == 'music' else 'tts',
                    request.scene_id if request.purpose.value == 'music' else request.output_artifact_id, request)
                response = await provider.generate(request)
                artifact = self._register_response(
                    job=active, response=response, modality=MediaModality.AUDIO,
                    artifact_type=ArtifactType.AUDIO, purpose=request.purpose.value,
                    input_ids=[item.artifact_id for item in request.references],
                    prompt=request.prompt_package, seed=request.seed,
                    duration=response.result.duration_seconds,
                    encoding=response.result.encoding,
                    extra={**response.result.provider_metadata,
                           'audio_request_fingerprint': key,
                           "sample_rate": response.result.sample_rate,
                           "channels": response.result.channels,
                           "loudness_lufs": response.result.loudness_lufs},
                )
                return ProviderResult(provider_request_id=request.request_id, success=True,
                                      artifact_ids=[artifact.artifact_id],
                                      metadata=response.result.provider_metadata)
            except BaseException as error:
                return normalize_media_provider_error(request.request_id, error)

        await self._execute(job, selection.provider_id, request.request_id, operation)
        return self._required(request.output_artifact_id,job_id=job.job_id)

    async def post_process(self, job: GenerationJob, request: PostProductionRequest) -> Artifact:
        selection = await self.router.select(MediaRoutingRequest(
            modality=MediaModality.POST, task="post", resource_class=request.resource_class,
        ))
        provider = self.providers.get(selection.provider_id)
        if not isinstance(provider, PostProcessor):
            raise TypeError("selected provider does not implement PostProcessor")
        if selection.provider_id == "ffmpeg-post":
            from movie_agent.media.post_runtime import run_real_post
            return await run_real_post(self, provider, job, request)

        async def operation(active: GenerationJob) -> ProviderResult:
            try:
                response = await provider.process(request)
                artifact = self._register_response(
                    job=active, response=response, modality=MediaModality.VIDEO,
                    artifact_type=ArtifactType.FINAL_FILM, purpose="final_film",
                    input_ids=[clip.artifact_id for track in request.timeline.video_tracks for clip in track.clips]
                              + [cue.artifact_id for track in request.timeline.audio_tracks for cue in track.cues],
                    prompt=None, seed=None, dimensions=response.result.dimensions,
                    duration=response.result.duration_seconds, encoding=response.result.encoding,
                )
                return ProviderResult(provider_request_id=request.request_id, success=True,
                                      artifact_ids=[artifact.artifact_id],
                                      metadata=response.result.provider_metadata)
            except BaseException as error:
                return normalize_media_provider_error(request.request_id, error)

        await self._execute(job, selection.provider_id, request.request_id, operation)
        return self._required(request.output_artifact_id,job_id=job.job_id)

    async def _execute(
        self,
        job: GenerationJob,
        provider_id: str,
        request_id: str,
        operation: Callable,
        preflight_error: Exception | None = None,
    ) -> GenerationJob:
        if self.job_manager is None:
            raise RuntimeError("media runtime has no bound JobManager")
        if self.runtime_coordinator:
            self.runtime_coordinator.bind(job.project_id, self.event_bus, self.trace_id)
        self._request_routes[job.job_id] = (provider_id, request_id)
        job = job.model_copy(update={"provider_id": provider_id, "activity": "provider activity"})
        known = {item.job_id for item in self.job_manager.all()}
        if job.job_id not in known:
            self.job_manager.add(job)
        async def leased_operation(active):
            try:
                if preflight_error:
                    raise preflight_error
                capabilities = await self.providers.get(provider_id).capabilities()
                coordinator = self.runtime_coordinator
                if capabilities.requires_resource_lease and (provider_id == "qwen3_vl" or not (coordinator and not coordinator.settings.enabled)):
                    valid = coordinator and any(
                        lease.job_id == active.job_id and lease.project_id == active.project_id
                        and lease.service_id == coordinator.service_for(provider_id)
                        and lease.status.value == "executing" for lease in coordinator.active_leases())
                    if not valid:
                        raise ProviderFailure("heavy provider requires an executing ResourceLease",
                                              ProviderErrorType.MODEL_NOT_READY)
                return await operation(active)
            except BaseException as error:
                return normalize_media_provider_error(request_id, error)
        completed = await LocalJobExecutor(self.job_manager).execute(job.job_id, leased_operation,
            manage_resources=preflight_error is None and provider_id != "ffmpeg-post")
        if completed.status != JobStatus.SUCCEEDED:
            # Do not expose raw SDK/remote details through the workflow surface.
            if completed.status == JobStatus.CANCELLED:
                error_type = ProviderErrorType.CANCELLED
            elif completed.status == JobStatus.WAITING_RESOURCE:
                from movie_agent.model_services.coordinator import ResourceAdmissionWait
                raise ResourceAdmissionWait(completed.failure_reason or 'Resource admission pending')
            else:
                prefix = "media_provider_"
                normalized = completed.failure_reason or ""
                try:
                    error_type = ProviderErrorType(
                        normalized.removeprefix(prefix)
                    ) if normalized.startswith(prefix) else ProviderErrorType.GENERATION_FAILED
                except ValueError:
                    error_type = ProviderErrorType.GENERATION_FAILED
            message = (
                "PROVIDER_UNAVAILABLE"
                if error_type == ProviderErrorType.UNAVAILABLE
                else error_type.value.upper()
            )
            raise ProviderFailure(message, error_type)
        completed = completed.model_copy(update={
            "output_artifact_ids": list(completed.related_artifact_ids),
            "activity": "completed",
            "progress_is_determinate": False,
        })
        self.job_manager._jobs[job.job_id] = completed
        return completed

    async def cancel(self, job_id: str) -> bool:
        """Request local cancellation and forward best-effort provider cancellation."""

        if self.job_manager is None:
            raise RuntimeError("media runtime has no bound JobManager")
        self.job_manager.request_cancel(job_id)
        route = self._request_routes.get(job_id)
        if route is None:
            return False
        provider_id, request_id = route
        dispatched = await self.providers.get(provider_id).cancel(request_id)
        self.job_manager.mark_remote_cancel_dispatched(job_id, dispatched)
        return dispatched

    def _register_native_video_audio(
        self,
        *,
        job: GenerationJob,
        response: ProviderMediaResponse,
        output,
        input_ids: list[str],
        prompt,
        seed: int | None,
    ) -> Artifact:
        payload = next((item for item in response.payloads if item.artifact_id == output.artifact_id), None)
        if payload is None:
            raise ValueError("provider result did not include a declared native audio payload")
        version = len(self.artifact_store.list_versions(output.artifact_id)) + 1
        binary = self.binary_store.put(
            output.artifact_id, version, payload.content,
            mime_type=payload.mime_type, extension=payload.extension,
        )
        media = MediaArtifactMetadata(
            modality=MediaModality.AUDIO,
            purpose=payload.purpose,
            duration=MediaDuration(seconds=output.duration_seconds) if output.duration_seconds else None,
            encoding=output.encoding,
            mock=bool(response.result.provider_metadata.get("mock")),
            test_asset=bool(response.result.provider_metadata.get("test_asset")),
            preview_artifact_id=output.artifact_id,
            waveform=[],
        )
        metadata = {
            "media": media.model_dump(mode="json"),
            "mime_type": payload.mime_type,
            "size_bytes": binary.size,
            "mock": media.mock,
            "duration_seconds": output.duration_seconds,
            "sample_rate": output.sample_rate,
            "channels": output.channels,
            "authoritative_speech": False,
            "native_audio_semantics": "production sound / ambience / foley-like sound; non-canonical dialogue",
        }
        parent_ids = list(dict.fromkeys([
            response.result.primary_artifact_id,
            *job.input_artifact_ids,
            *input_ids,
        ]))
        provenance = Provenance(
            role="Media Runtime",
            tool=f"{response.result.provider_id}_{payload.purpose}",
            provider_id=response.result.provider_id,
            model_service_id=response.result.model_service_id,
            prompt_package_id=prompt.prompt_package_id,
            compiler_id=prompt.compiler_id,
            compiler_version=prompt.compiler_version,
            project_id=job.project_id,
            scene_id=job.scene_id,
            shot_id=job.shot_id,
            input_artifact_ids=parent_ids,
            generation_strategy=job.strategy_type,
            seed=seed,
            parameters={
                **job.provenance.parameters,
                **response.result.provenance.parameters,
                **response.result.provider_metadata,
                "completed_at": response.result.completed_at.isoformat(),
            },
            repair_plan_ids=job.provenance.repair_plan_ids,
            evaluation_ids=job.provenance.evaluation_ids,
        )
        artifact = Artifact(
            artifact_id=output.artifact_id,
            artifact_type=ArtifactType.AUDIO,
            uri=binary.uri,
            version=version,
            source_job_id=job.job_id,
            parent_artifact_ids=parent_ids,
            metadata=metadata,
            provenance=provenance,
        )
        self.artifact_store.register(artifact)
        self._artifact_event(artifact, job)
        return artifact

    def _register_related_video_payload(
        self,
        *,
        job: GenerationJob,
        response: ProviderMediaResponse,
        payload: BinaryPayload,
        input_ids: list[str],
        prompt,
        seed: int | None,
    ) -> Artifact:
        if payload.mime_type.startswith("image/"):
            modality, artifact_type = MediaModality.IMAGE, ArtifactType.IMAGE
        elif payload.mime_type.startswith("audio/"):
            modality, artifact_type = MediaModality.AUDIO, ArtifactType.AUDIO
        elif payload.mime_type.startswith("video/"):
            modality, artifact_type = MediaModality.VIDEO, ArtifactType.VIDEO
        else:
            raise ValueError("provider returned an unsupported related media payload")
        version = len(self.artifact_store.list_versions(payload.artifact_id)) + 1
        binary = self.binary_store.put(
            payload.artifact_id, version, payload.content,
            mime_type=payload.mime_type, extension=payload.extension,
        )
        encoding = MediaEncoding(
            mime_type=payload.mime_type, format=payload.extension, codec=None
        )
        media = MediaArtifactMetadata(
            modality=modality,
            purpose=payload.purpose,
            encoding=encoding,
            mock=bool(response.result.provider_metadata.get("mock")),
            test_asset=bool(response.result.provider_metadata.get("test_asset")),
            preview_artifact_id=payload.artifact_id,
        )
        parameters = {
            **job.provenance.parameters,
            **response.result.provenance.parameters,
            **response.result.provider_metadata,
            "completed_at": response.result.completed_at.isoformat(),
        }
        artifact = Artifact(
            artifact_id=payload.artifact_id,
            artifact_type=artifact_type,
            uri=binary.uri,
            version=version,
            source_job_id=job.job_id,
            parent_artifact_ids=list(dict.fromkeys([*job.input_artifact_ids, *input_ids])),
            metadata={
                "media": media.model_dump(mode="json"),
                "mime_type": payload.mime_type,
                "size_bytes": binary.size,
                "mock": media.mock,
            },
            provenance=Provenance(
                role="Media Runtime",
                tool=f"{response.result.provider_id}_{payload.purpose}",
                provider_id=response.result.provider_id,
                model_service_id=response.result.model_service_id,
                prompt_package_id=prompt.prompt_package_id,
                compiler_id=prompt.compiler_id,
                compiler_version=prompt.compiler_version,
                project_id=job.project_id,
                scene_id=job.scene_id,
                shot_id=job.shot_id,
                input_artifact_ids=list(dict.fromkeys([*job.input_artifact_ids, *input_ids])),
                generation_strategy=job.strategy_type,
                seed=seed,
                parameters=parameters,
                repair_plan_ids=job.provenance.repair_plan_ids,
                evaluation_ids=job.provenance.evaluation_ids,
            ),
        )
        self.artifact_store.register(artifact)
        self._artifact_event(artifact, job)
        return artifact

    def _register_response(
        self,
        *,
        job: GenerationJob,
        response: ProviderMediaResponse,
        modality: MediaModality,
        artifact_type: ArtifactType,
        purpose: str,
        input_ids: list[str],
        prompt,
        seed: int | None,
        encoding: MediaEncoding,
        dimensions=None,
        duration: float | None = None,
        extra: dict | None = None,
    ) -> Artifact:
        primary = response.result.primary_artifact_id
        payload = next((item for item in response.payloads if item.artifact_id == primary), None)
        if payload is None:
            raise ValueError("provider result did not include the primary binary payload")
        version = len(self.artifact_store.list_versions(primary)) + 1
        thumbnail_id: str | None = None
        if artifact_type in {ArtifactType.VIDEO, ArtifactType.FINAL_FILM} and response.result.provider_id != "ffmpeg-post":
            thumbnail_id = f"thumbnail_{primary}"
            thumbnail_version = len(self.artifact_store.list_versions(thumbnail_id)) + 1
            thumbnail_binary = self.binary_store.put(thumbnail_id, thumbnail_version, _png(),
                mime_type="image/png", extension="png")
            thumbnail_media = MediaArtifactMetadata(
                modality=MediaModality.IMAGE, purpose="thumbnail",
                encoding=MediaEncoding(mime_type="image/png", format="png", codec="png"),
                mock=True, test_asset=True, preview_artifact_id=thumbnail_id,
            )
            thumbnail = Artifact(
                artifact_id=thumbnail_id, artifact_type=ArtifactType.IMAGE,
                uri=thumbnail_binary.uri, version=thumbnail_version,
                source_job_id=job.job_id, parent_artifact_ids=[primary],
                metadata={"media": thumbnail_media.model_dump(mode="json"),
                          "mime_type": "image/png", "size_bytes": thumbnail_binary.size,
                          "mock": True},
                provenance=Provenance(role="Media Runtime", tool="deterministic_thumbnail",
                    provider_id=response.result.provider_id, project_id=job.project_id,
                    scene_id=job.scene_id, shot_id=job.shot_id, input_artifact_ids=[primary]),
            )
            self.artifact_store.register(thumbnail)
            self._artifact_event(thumbnail, job)
        binary = self.binary_store.put(primary, version, payload.content,
                                       mime_type=payload.mime_type, extension=payload.extension)
        media = MediaArtifactMetadata(
            modality=modality, purpose=purpose, dimensions=dimensions,
            duration=MediaDuration(seconds=duration) if duration else None,
            encoding=encoding, mock=bool(response.result.provider_metadata.get("mock")),
            test_asset=bool(response.result.provider_metadata.get("test_asset")),
            preview_artifact_id=primary, thumbnail_artifact_id=thumbnail_id,
            waveform=[0.0, 0.15, -0.1, 0.2, -0.05, 0.0] if modality == MediaModality.AUDIO and response.result.provider_metadata.get('mock') else [],
        )
        metadata = {"media": media.model_dump(mode="json"), "mime_type": payload.mime_type,
                    "size_bytes": binary.size, "mock": media.mock}
        if modality in {MediaModality.AUDIO, MediaModality.IMAGE, MediaModality.VIDEO}:
            from movie_agent.media.post import stream_hash
            with self.binary_store.open(binary.uri) as stream:
                metadata['sha256'] = stream_hash(stream)
        if dimensions:
            metadata.update({"width": dimensions.width, "height": dimensions.height})
        if duration:
            metadata["duration_seconds"] = duration
        metadata.update(extra or {})
        provenance = Provenance(
            role="Media Runtime", tool=f"{response.result.provider_id}_{purpose}",
            provider_id=response.result.provider_id,
            model_service_id=response.result.model_service_id,
            prompt_package_id=prompt.prompt_package_id if prompt else None,
            compiler_id=prompt.compiler_id if prompt else None,
            compiler_version=prompt.compiler_version if prompt else None,
            project_id=job.project_id, scene_id=job.scene_id, shot_id=job.shot_id,
            input_artifact_ids=list(dict.fromkeys([*job.input_artifact_ids, *input_ids])),
            generation_strategy=job.strategy_type, seed=seed,
            parameters={**job.provenance.parameters, **response.result.provenance.parameters,
                        **response.result.provider_metadata,
                        "completed_at": response.result.completed_at.isoformat()},
            repair_plan_ids=job.provenance.repair_plan_ids,
            evaluation_ids=job.provenance.evaluation_ids,
        )
        artifact = Artifact(
            artifact_id=primary, artifact_type=artifact_type, uri=binary.uri, version=version,
            source_job_id=job.job_id,
            parent_artifact_ids=list(dict.fromkeys([*job.input_artifact_ids, *input_ids])),
            metadata=metadata, provenance=provenance,
        )
        self.artifact_store.register(artifact)
        self._artifact_event(artifact, job)
        return artifact

    def _artifact_event(self, artifact: Artifact, job: GenerationJob) -> None:
        self._emit(EventType.ARTIFACT_CREATED, job.project_id,
                   {"artifact_id": artifact.artifact_id,
                    "artifact_type": artifact.artifact_type.value,
                    "version": artifact.version}, job=job)

    def _emit(self, event_type: EventType, project_id: str, payload: dict, *, job: GenerationJob) -> None:
        self.event_bus.emit(EventEnvelope(
            event_type=event_type, project_id=project_id, trace_id=self.trace_id,
            node_id=job.node_id, job_id=job.job_id, payload=payload,
        ))

    def _required(self, artifact_id: str, *, job_id: str | None = None) -> Artifact:
        artifact = (next((a for a in reversed(self.artifact_store.list_versions(artifact_id))
                         if a.source_job_id==job_id),None) if job_id is not None else self.artifact_store.get(artifact_id))
        if artifact is None:
            raise LookupError(artifact_id)
        return artifact
