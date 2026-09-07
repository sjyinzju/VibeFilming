"""Model-neutral ComfyUI VideoProvider built from versioned workflow profiles."""

from __future__ import annotations

import inspect
import json
import subprocess
import time
import wave
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from movie_agent.comfyui import (
    ComfyUIAssetBridge,
    ComfyUIClient,
    ComfyUIExecutionEventAdapter,
    ComfyUIExecutionUpdate,
    ComfyUIOutputSource,
    ComfyUIWorkflowCompiler,
    ComfyUIWorkflowRegistry,
    validate_template_binding,
)
from movie_agent.domain import GenerationStrategyType, ProviderErrorType, ProviderKind, QualityProfile, ResourceClass
from movie_agent.domain.base import Provenance, utc_now
from movie_agent.media.contracts import (
    AudioPurpose,
    GeneratedAudioOutput,
    MediaDimensions,
    MediaEncoding,
    MediaGenerationStrategy,
    MediaModality,
    ProviderCapabilities,
    ResourceProfile,
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from movie_agent.media.transport import MediaReferenceBinaryResolver
from movie_agent.media.execution_graph import (
    ProviderExecutionGraphUpdate,
    ProviderExecutionNodeState,
    apply_provider_execution_update,
    build_provider_execution_graph,
)
from movie_agent.providers.base import ProviderFailure
from movie_agent.providers.media import (
    BinaryPayload,
    MediaProviderProgress,
    MediaProviderProgressCallback,
    ProviderMediaResponse,
    VideoProvider,
)


class ComfyUIVideoProvider(VideoProvider):
    provider_id = "comfyui-video"

    def __init__(
        self,
        *,
        client: ComfyUIClient,
        workflows: ComfyUIWorkflowRegistry,
        workflow_profile: str,
        resolver: MediaReferenceBinaryResolver | None,
        runtime_capabilities: VideoCapabilities | None = None,
        execution_adapter: ComfyUIExecutionEventAdapter | None = None,
        validate_remote_schema: bool = True,
        test_mode: bool = False,
    ) -> None:
        self.client = client
        self.workflows = workflows
        self.workflow_profile = workflow_profile
        self.resolver = resolver
        self.runtime_capabilities = runtime_capabilities or VideoCapabilities()
        self.execution_adapter = execution_adapter
        self.validate_remote_schema = validate_remote_schema
        self.test_mode = test_mode
        self._status: dict[str, ComfyUIExecutionUpdate | VideoGenerationResult] = {}
        self._remote_prompts: dict[str, str] = {}
        self._execution_graphs = {}

    async def health(self) -> bool:
        return await self.client.health()

    async def capabilities(self) -> ProviderCapabilities:
        try:
            _, template, _ = self.workflows.resolve(self.workflow_profile)
            supported = set(template.supported_capabilities)
        except LookupError:
            supported = set()
        runtime = self.runtime_capabilities

        def enabled(name: str) -> bool:
            return bool(getattr(runtime, name)) and name in supported

        video = VideoCapabilities(
            text_to_video=enabled("text_to_video"),
            image_to_video=enabled("image_to_video"),
            video_to_video=enabled("video_to_video"),
            video_extend=enabled("video_extend"),
            first_frame=enabled("first_frame"),
            last_frame=enabled("last_frame"),
            first_last_frame=enabled("first_last_frame"),
            multi_reference=enabled("multi_reference"),
            camera_control=enabled("camera_control"),
            audio_generation=enabled("audio_generation"),
            max_duration_seconds=runtime.max_duration_seconds,
            max_width=runtime.max_width,
            max_height=runtime.max_height,
            supported_fps=runtime.supported_fps,
        )
        resources = [
            ResourceProfile(resource_class=item, supports_concurrency=item != ResourceClass.EXCLUSIVE,
                            requires_exclusive_runtime=item == ResourceClass.EXCLUSIVE)
            for item in ResourceClass
        ]
        return ProviderCapabilities(
            provider_id=self.provider_id,
            kind=ProviderKind.VIDEO,
            modalities=[MediaModality.VIDEO],
            tasks=["video"],
            video=video,
            resource_profiles=resources,
            quality_profiles=list(QualityProfile),
            supports_cancellation=True,
        )

    async def status(self, request_id: str):
        return self._status.get(request_id)

    async def cancel(self, request_id: str) -> bool:
        prompt_id = self._remote_prompts.get(request_id)
        if prompt_id is None:
            return False
        return await self.client.cancel_job(prompt_id)

    async def generate(
        self,
        request: VideoGenerationRequest,
        *,
        strategy: MediaGenerationStrategy | None = None,
        on_progress: MediaProviderProgressCallback | None = None,
    ) -> ProviderMediaResponse[VideoGenerationResult]:
        try:
            profile, template, manifest = self.workflows.resolve(self.workflow_profile)
        except LookupError as error:
            raise ProviderFailure(
                f"ComfyUI workflow profile is not installed: {self.workflow_profile}",
                ProviderErrorType.UNSUPPORTED_CAPABILITY,
            ) from error
        if self.resolver is None:
            raise ProviderFailure("ComfyUI Artifact resolver is not configured", ProviderErrorType.UNAVAILABLE)
        strategy = strategy or self._strategy_from_request(request)
        capabilities = await self.capabilities()
        started = time.perf_counter()
        stats = await self.client.system_stats()
        if self.validate_remote_schema:
            validate_template_binding(
                template,
                manifest,
                await self.client.object_info(sorted({
                    node.class_type for node in template.api_workflow.values()
                })),
            )
        input_assets = await ComfyUIAssetBridge(self.client, self.resolver).upload_video_inputs(request)
        spec = ComfyUIWorkflowCompiler().compile(
            request=request,
            strategy=strategy,
            capabilities=capabilities,
            input_assets=input_assets,
            template=template,
            binding_manifest=manifest,
            model_profile=profile.model_profile,
        )
        execution_graph = build_provider_execution_graph(
            execution_id=request.request_id,
            provider=self.provider_id,
            workflow_template_id=spec.template_id,
            workflow_template_version=spec.template_version,
            workflow_template_hash=spec.template_hash,
            binding_manifest_id=spec.binding_manifest_id,
            binding_manifest_version=spec.binding_manifest_version,
            prompt=spec.prompt,
            node_metadata=template.node_metadata,
            parent_node_id="shot_production",
            parent_job_id=request.job_id,
            project_id=request.project_id,
            scene_id=request.scene_id,
            shot_id=request.shot_id,
        )
        self._execution_graphs[request.request_id] = execution_graph

        async def progress(update: ComfyUIExecutionUpdate) -> None:
            nonlocal execution_graph
            self._status[request.request_id] = update
            for cached_node_id in update.cached_node_ids:
                execution_graph = apply_provider_execution_update(
                    execution_graph,
                    ProviderExecutionGraphUpdate(
                        execution_id=execution_graph.execution_id,
                        remote_event="execution_cached",
                        remote_node_id=cached_node_id,
                        runtime_state=ProviderExecutionNodeState.SUCCEEDED,
                    ),
                )
            state = {
                "executing": ProviderExecutionNodeState.RUNNING,
                "progress_state": ProviderExecutionNodeState.RUNNING,
                "execution_error": ProviderExecutionNodeState.FAILED,
                "execution_interrupted": ProviderExecutionNodeState.INTERRUPTED,
            }.get(update.remote_event)
            graph_update = ProviderExecutionGraphUpdate(
                execution_id=execution_graph.execution_id,
                remote_event=update.remote_event,
                remote_node_id=update.node_id,
                runtime_state=state,
                progress=update.progress,
                progress_is_determinate=update.progress_is_determinate,
                activity=update.activity,
                error=update.error,
                timestamp=utc_now(),
            )
            execution_graph = apply_provider_execution_update(execution_graph, graph_update)
            self._execution_graphs[request.request_id] = execution_graph
            if on_progress is not None:
                result = on_progress(MediaProviderProgress(
                    status=update.status,
                    activity=update.activity,
                    remote_event=update.remote_event,
                    progress=update.progress,
                    progress_is_determinate=update.progress_is_determinate,
                    node_id=update.node_id,
                    provider_execution_graph=(
                        execution_graph.model_dump(mode="json")
                        if update.remote_event in {
                            "prompt_submitted", "execution_cached", "execution_success",
                            "execution_error", "execution_interrupted",
                        }
                        else None
                    ),
                    provider_execution_update=(
                        graph_update.model_dump(mode="json")
                        if update.remote_event not in {"execution_cached"}
                        else None
                    ),
                ))
                if inspect.isawaitable(result):
                    await result

        async def submitted(value) -> None:
            nonlocal execution_graph
            self._remote_prompts[request.request_id] = value.prompt_id
            execution_graph = execution_graph.model_copy(update={
                "execution_id": value.prompt_id,
                "remote_prompt_id": value.prompt_id,
            })
            self._execution_graphs[request.request_id] = execution_graph

        submission = await self.client.execute_prompt(
            spec.prompt,
            execution_adapter=self.execution_adapter,
            on_update=progress,
            on_submitted=submitted,
        )
        history = await self.client.history(submission.prompt_id)
        payloads: list[BinaryPayload] = []
        audio_outputs: list[GeneratedAudioOutput] = []
        artifact_ids: list[str] = []
        primary_encoding: MediaEncoding | None = None
        primary_codec: str | None = None
        purpose_counts: dict[str, int] = {}
        remote_payloads: dict[tuple[str, str, str], bytes] = {}
        for declaration in spec.expected_outputs:
            remote_files = self.client.output_files(
                history, node_id=declaration.node_id, history_key=declaration.history_key
            )
            if not remote_files:
                if declaration.required:
                    raise ProviderFailure(
                        f"ComfyUI required output is missing: {declaration.node_id}",
                        ProviderErrorType.GENERATION_FAILED,
                    )
                continue
            for remote_file in remote_files:
                remote_key = (remote_file.filename, remote_file.subfolder, remote_file.type)
                if remote_key not in remote_payloads:
                    remote_payloads[remote_key] = await self.client.view(remote_file)
                content = remote_payloads[remote_key]
                if declaration.source == ComfyUIOutputSource.MUXED_AUDIO:
                    content = self._extract_muxed_audio(content)
                self._validate_output(content, declaration.mime_type)
                if declaration.primary:
                    artifact_id = request.output_artifact_id
                else:
                    count = purpose_counts.get(declaration.purpose, 0)
                    purpose_counts[declaration.purpose] = count + 1
                    suffix = "" if count == 0 else f"_{count + 1}"
                    artifact_id = f"{request.output_artifact_id}_{declaration.purpose}{suffix}"
                encoding = MediaEncoding(
                    mime_type=declaration.mime_type,
                    format=declaration.extension,
                    codec=declaration.codec,
                )
                artifact_ids.append(artifact_id)
                payloads.append(BinaryPayload(
                    artifact_id=artifact_id,
                    content=content,
                    mime_type=declaration.mime_type,
                    extension=declaration.extension,
                    purpose=declaration.purpose,
                ))
                if declaration.primary:
                    primary_encoding, primary_codec = encoding, declaration.codec
                elif declaration.modality == MediaModality.AUDIO:
                    duration, sample_rate, channels = self._audio_properties(content, declaration.mime_type)
                    audio_outputs.append(GeneratedAudioOutput(
                        artifact_id=artifact_id,
                        purpose=AudioPurpose.GENERATED_NATIVE_AUDIO,
                        duration_seconds=duration,
                        sample_rate=sample_rate,
                        channels=channels,
                        encoding=encoding,
                    ))
        if primary_encoding is None:
            raise ProviderFailure("ComfyUI primary video output is missing", ProviderErrorType.GENERATION_FAILED)

        execution_graph = execution_graph.model_copy(update={
            "nodes": [
                node.model_copy(update={"output_artifact_ids": artifact_ids})
                if node.remote_node_id == "92" else node
                for node in execution_graph.nodes
            ],
        })
        self._execution_graphs[request.request_id] = execution_graph

        system = stats.get("system", {})
        provider_metadata = {
            "mock": self.test_mode,
            "test_asset": self.test_mode,
            "comfyui_runtime_version": system.get("comfyui_version"),
            "comfyui_prompt_id": submission.prompt_id,
            "workflow_profile": self.workflow_profile,
            "workflow_template_id": spec.template_id,
            "workflow_template_version": spec.template_version,
            "workflow_hash": spec.template_hash,
            "binding_manifest_id": spec.binding_manifest_id,
            "binding_manifest_version": spec.binding_manifest_version,
            "model_profile": spec.model_profile,
            "seed": request.seed,
            "dimensions": {"width": request.width, "height": request.height},
            "fps": float(request.fps),
            "duration_seconds": float(request.duration_seconds),
            "frame_count": round(float(request.duration_seconds) * float(request.fps)),
            "prompt_parameters": {
                "positive_prompt": request.prompt_package.positive_prompt,
                "negative_prompt": request.prompt_package.negative_prompt,
                "negative_prompt_policy": (
                    "inline_guidance" if manifest.inline_negative_prompt else "separate_or_unsupported"
                ),
            },
            "input_artifacts": [
                {"semantic_slot": item.semantic_slot, "artifact_id": item.artifact_id,
                 "version": item.artifact_version, "sha256": item.sha256}
                for item in input_assets
            ],
            "timings": {"total_seconds": time.perf_counter() - started},
            "native_audio_derivation": "ffmpeg_audio_stream_copy_from_h3_muxed_video",
            "provider_execution_graph": execution_graph.model_dump(mode="json"),
        }
        result = VideoGenerationResult(
            request_id=request.request_id,
            artifact_ids=artifact_ids,
            primary_artifact_id=request.output_artifact_id,
            provider_id=self.provider_id,
            duration_seconds=request.duration_seconds,
            fps=request.fps,
            dimensions=MediaDimensions(
                width=request.width, height=request.height, aspect_ratio=request.aspect_ratio
            ),
            frame_count=round(float(request.duration_seconds) * float(request.fps)),
            codec=primary_codec or primary_encoding.codec or primary_encoding.format,
            encoding=primary_encoding,
            native_audio_outputs=audio_outputs,
            provider_metadata=provider_metadata,
            provenance=Provenance(parameters=provider_metadata),
        )
        self._status[request.request_id] = result
        return ProviderMediaResponse(result=result, payloads=tuple(payloads))

    @staticmethod
    def _strategy_from_request(request: VideoGenerationRequest) -> MediaGenerationStrategy:
        return MediaGenerationStrategy(
            strategy_type=GenerationStrategyType(request.mode.value),
            reason="Derived from the already-selected provider-neutral video mode.",
            required_capabilities=[item.capability for item in request.required_capabilities if item.required],
        )

    @staticmethod
    def _validate_output(content: bytes, mime_type: str) -> None:
        valid = True
        if mime_type == "video/mp4":
            valid = len(content) >= 12 and content[4:8] == b"ftyp"
        elif mime_type == "audio/wav":
            valid = len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WAVE"
        elif mime_type == "audio/mp4":
            valid = len(content) >= 12 and content[4:8] == b"ftyp"
        if not valid:
            raise ProviderFailure("ComfyUI returned corrupt media", ProviderErrorType.MEDIA_CORRUPT)

    @staticmethod
    def _audio_properties(content: bytes, mime_type: str) -> tuple[float | None, int | None, int | None]:
        if mime_type == "audio/wav":
            try:
                with wave.open(BytesIO(content), "rb") as decoded:
                    rate = decoded.getframerate()
                    return decoded.getnframes() / rate, rate, decoded.getnchannels()
            except (EOFError, wave.Error) as error:
                raise ProviderFailure("ComfyUI returned corrupt WAV audio", ProviderErrorType.MEDIA_CORRUPT) from error
        if mime_type == "audio/mp4":
            with TemporaryDirectory(prefix="movie-agent-probe-") as directory:
                source = Path(directory) / "native.m4a"
                source.write_bytes(content)
                try:
                    completed = subprocess.run(
                        [
                            "ffprobe", "-v", "error", "-select_streams", "a:0",
                            "-show_entries", "stream=sample_rate,channels,duration",
                            "-of", "json", str(source),
                        ],
                        capture_output=True, text=True, timeout=30, check=False,
                    )
                except (FileNotFoundError, subprocess.TimeoutExpired) as error:
                    raise ProviderFailure(
                        "FFprobe is unavailable for native audio validation",
                        ProviderErrorType.UNAVAILABLE,
                    ) from error
                if completed.returncode != 0:
                    raise ProviderFailure(
                        "Extracted native audio could not be probed",
                        ProviderErrorType.MEDIA_CORRUPT,
                    )
                streams = json.loads(completed.stdout).get("streams", [])
                if not streams:
                    raise ProviderFailure("H3 output has no native audio stream", ProviderErrorType.MEDIA_CORRUPT)
                stream = streams[0]
                return (
                    float(stream["duration"]) if stream.get("duration") else None,
                    int(stream["sample_rate"]) if stream.get("sample_rate") else None,
                    int(stream["channels"]) if stream.get("channels") else None,
                )
        return None, None, None

    @staticmethod
    def _extract_muxed_audio(content: bytes) -> bytes:
        """Deterministically copy H3's native audio stream without re-encoding it."""

        with TemporaryDirectory(prefix="movie-agent-h3-audio-") as directory:
            source = Path(directory) / "source.mp4"
            target = Path(directory) / "native.m4a"
            source.write_bytes(content)
            try:
                completed = subprocess.run(
                    [
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(source), "-map", "0:a:0", "-c:a", "copy", str(target),
                    ],
                    capture_output=True, timeout=120, check=False,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired) as error:
                raise ProviderFailure(
                    "FFmpeg is unavailable for native audio extraction",
                    ProviderErrorType.UNAVAILABLE,
                ) from error
            if completed.returncode != 0 or not target.exists():
                raise ProviderFailure(
                    "H3 muxed video has no extractable native audio stream",
                    ProviderErrorType.MEDIA_CORRUPT,
                )
            return target.read_bytes()
