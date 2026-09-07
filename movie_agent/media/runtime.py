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
    ) -> None:
        self.artifact_store = artifact_store
        self.binary_store = binary_store
        self.providers = providers
        self.router = MediaRouter(providers)
        self.event_bus = event_bus
        self.trace_id = trace_id
        self.job_manager: JobManager | None = None
        self.inspections: list[VisionInspectionResult] = []
        self._request_routes: dict[str, tuple[str, str]] = {}

    def bind_jobs(self, manager: JobManager) -> None:
        self.job_manager = manager

    async def capabilities(self):
        await self.router.refresh()
        return self.router.capabilities.all()

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

        async def operation(active: GenerationJob) -> ProviderResult:
            nonlocal response
            try:
                selection = await self.router.select(MediaRoutingRequest(
                    modality=MediaModality.IMAGE, task="frame" if artifact_type == ArtifactType.FRAME else "image",
                    required_capabilities=[item.capability for item in request.required_capabilities if item.required],
                    quality_profile=request.quality_profile, resource_class=request.resource_class,
                ))
                provider = self.providers.get(selection.provider_id)
                if not isinstance(provider, ImageProvider):
                    raise TypeError("selected provider does not implement ImageProvider")
                self._request_routes[active.job_id] = (provider.provider_id, request.request_id)
                active = active.model_copy(update={"provider_id": provider.provider_id})
                self.job_manager._jobs[active.job_id] = active
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

        await self._execute(job, initial_provider_id, request.request_id, operation)
        return self._required(request.output_artifact_id)

    async def generate_video(self, job: GenerationJob, request: VideoGenerationRequest) -> Artifact:
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

        async def operation(active: GenerationJob) -> ProviderResult:
            try:
                strategy = MediaGenerationStrategy(
                    strategy_type=GenerationStrategyType(active.strategy_type or request.mode.value),
                    reason="Persisted GenerationStrategy selected before provider routing.",
                    required_capabilities=list(dict.fromkeys([
                        *routing_capabilities,
                    ])),
                    input_artifact_ids=list(active.input_artifact_ids),
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

                response = await provider.generate(request, strategy=strategy, on_progress=progress)
                input_ids = list(dict.fromkeys([
                    *[item.artifact_id for item in request.references],
                    *[item.artifact_id for item in (
                        request.first_frame, request.last_frame, request.previous_shot
                    ) if item is not None],
                ]))
                artifact = self._register_response(
                    job=active, response=response, modality=MediaModality.VIDEO,
                    artifact_type=ArtifactType.VIDEO, purpose="shot_video",
                    input_ids=input_ids,
                    prompt=request.prompt_package, seed=request.seed,
                    dimensions=response.result.dimensions,
                    duration=response.result.duration_seconds,
                    encoding=response.result.encoding,
                    extra={"fps": request.fps, "frame_count": response.result.frame_count,
                           "codec": response.result.codec},
                )
                related = [artifact]
                for audio in response.result.native_audio_outputs:
                    related.append(self._register_native_video_audio(
                        job=active,
                        response=response,
                        output=audio,
                        input_ids=input_ids,
                        prompt=request.prompt_package,
                        seed=request.seed,
                    ))
                registered_ids = {item.artifact_id for item in related}
                for payload in response.payloads:
                    if payload.artifact_id not in registered_ids:
                        related.append(self._register_related_video_payload(
                            job=active,
                            response=response,
                            payload=payload,
                            input_ids=input_ids,
                            prompt=request.prompt_package,
                            seed=request.seed,
                        ))
                        registered_ids.add(payload.artifact_id)
                return ProviderResult(provider_request_id=request.request_id, success=True,
                                      artifact_ids=[item.artifact_id for item in related],
                                      metadata=response.result.provider_metadata)
            except BaseException as error:
                return normalize_media_provider_error(request.request_id, error)

        await self._execute(job, selection.provider_id, request.request_id, operation)
        return self._required(request.output_artifact_id)

    async def inspect(self, job: GenerationJob, request: VisionInspectionRequest) -> VisionInspectionResult:
        selection = await self.router.select(MediaRoutingRequest(
            modality=MediaModality.VISION, task="inspect",
            required_capabilities=["video" if request.video_artifact_id else "image"],
            resource_class=job.resource_class,
        ))
        provider = self.providers.get(selection.provider_id)
        if not isinstance(provider, VisionProvider):
            raise TypeError("selected provider does not implement VisionProvider")
        result: VisionInspectionResult | None = None
        self._emit(EventType.MEDIA_EVALUATION_STARTED, job.project_id,
                   {"request_id": request.request_id, "target_artifact_id":
                    request.video_artifact_id or request.image_artifact_id}, job=job)

        async def operation(_: GenerationJob) -> ProviderResult:
            nonlocal result
            try:
                result = await provider.inspect(request)
                return ProviderResult(provider_request_id=request.request_id, success=True)
            except BaseException as error:
                return normalize_media_provider_error(request.request_id, error)

        await self._execute(job, selection.provider_id, request.request_id, operation)
        if result is None:
            raise RuntimeError("vision provider completed without a result")
        self.inspections.append(result)
        self._emit(EventType.MEDIA_EVALUATION_COMPLETED, job.project_id,
                   {"result_id": result.result_id, "target_artifact_id": result.target_artifact_id,
                    "decision": result.decision.value}, job=job)
        return result

    async def generate_audio(self, job: GenerationJob, request: AudioRequestBase) -> Artifact:
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
                response = await provider.generate(request)
                artifact = self._register_response(
                    job=active, response=response, modality=MediaModality.AUDIO,
                    artifact_type=ArtifactType.AUDIO, purpose=request.purpose.value,
                    input_ids=[item.artifact_id for item in request.references],
                    prompt=request.prompt_package, seed=request.seed,
                    duration=response.result.duration_seconds,
                    encoding=response.result.encoding,
                    extra={"sample_rate": response.result.sample_rate,
                           "channels": response.result.channels,
                           "loudness_lufs": response.result.loudness_lufs},
                )
                return ProviderResult(provider_request_id=request.request_id, success=True,
                                      artifact_ids=[artifact.artifact_id],
                                      metadata=response.result.provider_metadata)
            except BaseException as error:
                return normalize_media_provider_error(request.request_id, error)

        await self._execute(job, selection.provider_id, request.request_id, operation)
        return self._required(request.output_artifact_id)

    async def post_process(self, job: GenerationJob, request: PostProductionRequest) -> Artifact:
        selection = await self.router.select(MediaRoutingRequest(
            modality=MediaModality.POST, task="post", resource_class=request.resource_class,
        ))
        provider = self.providers.get(selection.provider_id)
        if not isinstance(provider, PostProcessor):
            raise TypeError("selected provider does not implement PostProcessor")

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
        return self._required(request.output_artifact_id)

    async def _execute(
        self,
        job: GenerationJob,
        provider_id: str,
        request_id: str,
        operation: Callable,
    ) -> GenerationJob:
        if self.job_manager is None:
            raise RuntimeError("media runtime has no bound JobManager")
        self._request_routes[job.job_id] = (provider_id, request_id)
        job = job.model_copy(update={"provider_id": provider_id, "activity": "provider activity"})
        known = {item.job_id for item in self.job_manager.all()}
        if job.job_id not in known:
            self.job_manager.add(job)
        completed = await LocalJobExecutor(self.job_manager).execute(job.job_id, operation)
        if completed.status != JobStatus.SUCCEEDED:
            # Do not expose raw SDK/remote details through the workflow surface.
            if completed.status == JobStatus.CANCELLED:
                error_type = ProviderErrorType.CANCELLED
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
        if artifact_type in {ArtifactType.VIDEO, ArtifactType.FINAL_FILM}:
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
            waveform=[0.0, 0.15, -0.1, 0.2, -0.05, 0.0] if modality == MediaModality.AUDIO else [],
        )
        metadata = {"media": media.model_dump(mode="json"), "mime_type": payload.mime_type,
                    "size_bytes": binary.size, "mock": media.mock}
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

    def _required(self, artifact_id: str) -> Artifact:
        artifact = self.artifact_store.get(artifact_id)
        if artifact is None:
            raise LookupError(artifact_id)
        return artifact
