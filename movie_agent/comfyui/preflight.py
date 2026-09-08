"""Aggregated, model-neutral preflight for ComfyUI video workflow requests."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from movie_agent.domain import Artifact, CameraMotionType
from movie_agent.media.contracts import (
    CameraMotionSpec,
    MediaDimensions,
    MediaGenerationStrategy,
    MediaReference,
    ProviderCapabilities,
    TemporalControl,
    VideoGenerationMode,
    VideoGenerationRequest,
    VideoPreflightDisposition,
    VideoPreflightIssue,
    VideoPreflightResult,
)
from movie_agent.media.frame_planning import fit_canvas_to_provider_bounds

from .contracts import (
    ComfyUIWorkflowTemplate,
    WorkflowBindingManifest,
    camera_motion_is_prompt_encoded,
)


_OWNER = "movie_agent_core_video_preflight"
_NOOP_TOKENS = {"", "none", "n/a", "na", "null", "not applicable"}


class VideoGenerationPreflight:
    """Report every workflow incompatibility, then apply only deterministic adaptations."""

    def prepare(
        self,
        *,
        request: VideoGenerationRequest,
        strategy: MediaGenerationStrategy,
        capabilities: ProviderCapabilities,
        template: ComfyUIWorkflowTemplate,
        binding_manifest: WorkflowBindingManifest,
        input_artifacts: list[Artifact],
    ) -> VideoPreflightResult:
        artifacts = {(item.artifact_id, item.version): item for item in input_artifacts}
        by_id = {item.artifact_id: item for item in input_artifacts}
        multiple = self._dimension_multiple(template)
        canvas = self._negotiate_canvas(request, capabilities, by_id, multiple)
        initial = self._inspect(
            request, strategy, capabilities, template, binding_manifest,
            by_id, canvas, multiple,
        )

        first = self._materialize_reference(request.first_frame, artifacts, by_id)
        last = self._materialize_reference(request.last_frame, artifacts, by_id)
        previous = self._materialize_reference(request.previous_shot, artifacts, by_id)
        materialized = {
            (item.artifact_id, item.reference_type): item
            for item in (first, last, previous)
            if item is not None
        }
        references = [
            materialized.get((item.artifact_id, item.reference_type),
                             self._materialize_reference(item, artifacts, by_id) or item)
            for item in request.references
        ]
        seed = request.seed if request.seed is not None else self._stable_seed(request)
        motion = self._canonical_motion(request.camera_motion)
        effective = request.model_copy(update={
            "width": canvas[0],
            "height": canvas[1],
            "seed": seed,
            "camera_motion": motion,
            "first_frame": first,
            "last_frame": last,
            "previous_shot": previous,
            "references": references,
        }, deep=True)
        effective_canvas = self._negotiate_canvas(effective, capabilities, by_id, multiple)
        remaining = self._inspect(
            effective, strategy, capabilities, template, binding_manifest,
            by_id, effective_canvas, multiple,
        )
        reasons = []
        if (request.width, request.height) != (effective.width, effective.height):
            reasons.append(
                "Use the existing boundary frame canvas when it preserves aspect ratio, "
                "satisfies workflow alignment, and fits provider limits."
            )
        if request.seed is None:
            reasons.append("Materialize a deterministic Core-owned seed from canonical request identity and revision.")
        if request.camera_motion != effective.camera_motion:
            reasons.append("Canonicalize recognized static camera no-op tokens before provider compilation.")
        return VideoPreflightResult(
            original_request=request.model_copy(deep=True),
            effective_request=effective,
            initial_issues=initial,
            remaining_issues=remaining,
            compatible=not remaining,
            requested_delivery_dimensions=MediaDimensions(
                width=request.width, height=request.height, aspect_ratio=request.aspect_ratio
            ),
            effective_generation_dimensions=MediaDimensions(
                width=effective.width, height=effective.height, aspect_ratio=effective.aspect_ratio
            ),
            adaptation_reason=reasons,
            input_artifacts=[self._artifact_fact(item) for item in input_artifacts],
        )

    def _inspect(
        self,
        request: VideoGenerationRequest,
        strategy: MediaGenerationStrategy,
        capabilities: ProviderCapabilities,
        template: ComfyUIWorkflowTemplate,
        manifest: WorkflowBindingManifest,
        artifacts: dict[str, Artifact],
        canvas: tuple[int, int],
        multiple: int,
    ) -> list[VideoPreflightIssue]:
        issues: list[VideoPreflightIssue] = []
        video = capabilities.video
        bound = {item.semantic_slot for item in manifest.bindings}
        required_bound = {item.semantic_slot for item in manifest.bindings if item.required}

        def add(
            code: str,
            field: str,
            value: Any,
            requirement: str,
            disposition: VideoPreflightDisposition,
            resolved: Any = None,
        ) -> None:
            issues.append(VideoPreflightIssue(
                code=code,
                field_path=field,
                request_value=self._json(value),
                requirement=requirement,
                disposition=disposition,
                adaptation_owner=_OWNER,
                resolved_value=self._json(resolved),
            ))

        if request.mode != template.generation_mode:
            add("mode_mismatch", "mode", request.mode.value,
                f"workflow mode={template.generation_mode.value}",
                VideoPreflightDisposition.HARD_UNSUPPORTED)

        if video is None:
            add("capability_mismatch", "provider.video", None, "video capabilities are required",
                VideoPreflightDisposition.HARD_UNSUPPORTED)
        else:
            flags = {name for name, value in video.model_dump().items() if value is True}
            effective_capabilities = flags & set(template.supported_capabilities)
            required = {
                *strategy.required_capabilities,
                *(item.capability for item in request.required_capabilities if item.required),
                *template.required_capabilities,
            }
            for missing in sorted(required - effective_capabilities):
                add("capability_mismatch", "required_capabilities", missing,
                    f"provider/workflow must support {missing}",
                    VideoPreflightDisposition.HARD_UNSUPPORTED)
            if video.max_width and request.width > video.max_width:
                add("unsupported_width", "width", request.width,
                    f"max_width={video.max_width}", VideoPreflightDisposition.ADAPTABLE,
                    canvas[0])
            if video.max_height and request.height > video.max_height:
                add("unsupported_height", "height", request.height,
                    f"max_height={video.max_height}", VideoPreflightDisposition.ADAPTABLE,
                    canvas[1])
            if video.supported_fps and request.fps not in video.supported_fps:
                add("unsupported_fps", "fps", request.fps,
                    f"supported_fps={list(video.supported_fps)}",
                    VideoPreflightDisposition.HARD_UNSUPPORTED)
            if video.max_duration_seconds and request.duration_seconds > video.max_duration_seconds:
                add("unsupported_duration", "duration_seconds", request.duration_seconds,
                    f"max_duration_seconds={video.max_duration_seconds}",
                    VideoPreflightDisposition.HARD_UNSUPPORTED)

        if (request.width, request.height) != canvas:
            add("delivery_generation_canvas_mismatch", "width,height",
                [request.width, request.height],
                "generation canvas should reuse matching boundary-frame dimensions when compatible",
                VideoPreflightDisposition.ADAPTABLE, list(canvas))
        if request.width % multiple or request.height % multiple:
            add("unaligned_dimensions", "width,height", [request.width, request.height],
                f"workflow dimension_multiple={multiple}",
                VideoPreflightDisposition.ADAPTABLE, list(canvas))

        for field, reference, required in (
            ("first_frame", request.first_frame,
             request.mode in {VideoGenerationMode.FIRST_FRAME_TO_VIDEO,
                              VideoGenerationMode.FIRST_LAST_FRAME_TO_VIDEO}
             or "first_frame" in required_bound),
            ("last_frame", request.last_frame,
             request.mode == VideoGenerationMode.FIRST_LAST_FRAME_TO_VIDEO
             or "last_frame" in required_bound),
        ):
            if required and reference is None:
                add(f"missing_{field}", field, None, f"{field} is required",
                    VideoPreflightDisposition.HARD_UNSUPPORTED)
            elif reference is not None and reference.artifact_id not in artifacts:
                add("missing_artifact_metadata", field, reference.artifact_id,
                    "referenced Artifact metadata must be available",
                    VideoPreflightDisposition.HARD_UNSUPPORTED)

        first_dims = self._reference_dimensions(request.first_frame, artifacts)
        last_dims = self._reference_dimensions(request.last_frame, artifacts)
        if first_dims and last_dims and first_dims != last_dims:
            add("frame_dimension_mismatch", "first_frame,last_frame",
                [list(first_dims), list(last_dims)],
                "first and last frame dimensions must match",
                VideoPreflightDisposition.HARD_UNSUPPORTED)
        ratio = self._aspect_value(request.aspect_ratio)
        for field, dims in (("first_frame", first_dims), ("last_frame", last_dims)):
            if dims and ratio and abs(dims[0] / dims[1] - ratio) > 0.01:
                add("aspect_ratio_mismatch", field, list(dims),
                    f"frame aspect ratio must match {request.aspect_ratio}",
                    VideoPreflightDisposition.HARD_UNSUPPORTED)

        if "seed" in required_bound and request.seed is None:
            add("missing_required_seed", "seed", None, "workflow binding requires seed",
                VideoPreflightDisposition.ADAPTABLE, self._stable_seed(request))

        if "camera_motion" not in bound:
            motion = request.camera_motion
            if motion.motion_type == CameraMotionType.STATIC:
                for field in ("direction", "speed"):
                    value = getattr(motion, field)
                    if value is not None and self._is_noop(value):
                        add("camera_noop_token", f"camera_motion.{field}", value,
                            "static camera no-op must be null",
                            VideoPreflightDisposition.ADAPTABLE, None)
                if (motion.direction is not None and not self._is_noop(motion.direction)) or (
                    motion.speed is not None and not self._is_noop(motion.speed)
                ) or motion.path:
                    if not camera_motion_is_prompt_encoded(request, manifest):
                        add("unsupported_camera_motion", "camera_motion", motion.model_dump(mode="json"),
                            "workflow has neither a camera_motion binding nor verified prompt encoding",
                            VideoPreflightDisposition.HARD_UNSUPPORTED)
            elif not camera_motion_is_prompt_encoded(request, manifest):
                add("unsupported_camera_motion", "camera_motion", motion.model_dump(mode="json"),
                    "workflow has neither a camera_motion binding nor verified prompt encoding",
                    VideoPreflightDisposition.HARD_UNSUPPORTED)

        if "temporal_control" not in bound and (
            request.temporal_control.model_dump() != TemporalControl().model_dump()
        ):
            add("unsupported_temporal_control", "temporal_control",
                request.temporal_control.model_dump(mode="json"),
                "workflow has no temporal_control binding",
                VideoPreflightDisposition.HARD_UNSUPPORTED)
        declared_parameters = {
            item.semantic_slot.removeprefix("provider_parameters.")
            for item in manifest.bindings
            if item.semantic_slot.startswith("provider_parameters.")
        }
        for name in sorted(set(request.provider_parameters) - declared_parameters):
            add("unknown_provider_parameter", f"provider_parameters.{name}",
                request.provider_parameters[name], "parameter must be declared by the binding manifest",
                VideoPreflightDisposition.HARD_UNSUPPORTED)
        return issues

    def _negotiate_canvas(
        self,
        request: VideoGenerationRequest,
        capabilities: ProviderCapabilities,
        artifacts: dict[str, Artifact],
        multiple: int,
    ) -> tuple[int, int]:
        first = self._reference_dimensions(request.first_frame, artifacts)
        last = self._reference_dimensions(request.last_frame, artifacts)
        video = capabilities.video
        ratio = self._aspect_value(request.aspect_ratio)
        if first and last and first == last and self._canvas_valid(first, video, multiple, ratio):
            return first
        return fit_canvas_to_provider_bounds(
            request.width,
            request.height,
            max_width=video.max_width if video else None,
            max_height=video.max_height if video else None,
            dimension_multiple=multiple,
        )

    @staticmethod
    def _canvas_valid(
        dimensions: tuple[int, int], video, multiple: int, aspect_ratio: float | None
    ) -> bool:
        width, height = dimensions
        return (
            (not video or not video.max_width or width <= video.max_width)
            and (not video or not video.max_height or height <= video.max_height)
            and width % multiple == 0
            and height % multiple == 0
            and (aspect_ratio is None or abs(width / height - aspect_ratio) <= 0.01)
        )

    @staticmethod
    def _reference_dimensions(
        reference: MediaReference | None, artifacts: dict[str, Artifact]
    ) -> tuple[int, int] | None:
        if reference is None:
            return None
        artifact = artifacts.get(reference.artifact_id)
        if artifact is None:
            return ((reference.width, reference.height)
                    if reference.width and reference.height else None)
        media = artifact.metadata.get("media")
        dimensions = media.get("dimensions") if isinstance(media, dict) else None
        width = dimensions.get("width") if isinstance(dimensions, dict) else artifact.metadata.get("width")
        height = dimensions.get("height") if isinstance(dimensions, dict) else artifact.metadata.get("height")
        return (int(width), int(height)) if width and height else None

    @classmethod
    def _materialize_reference(
        cls,
        reference: MediaReference | None,
        artifacts: dict[tuple[str, int], Artifact],
        by_id: dict[str, Artifact],
    ) -> MediaReference | None:
        if reference is None:
            return None
        artifact = (artifacts.get((reference.artifact_id, reference.version))
                    if reference.version else by_id.get(reference.artifact_id))
        if artifact is None:
            return reference.model_copy(deep=True)
        dimensions = cls._reference_dimensions(reference, {artifact.artifact_id: artifact})
        return reference.model_copy(update={
            "version": artifact.version,
            "mime_type": artifact.metadata.get("mime_type"),
            "size_bytes": artifact.metadata.get("size_bytes"),
            "width": dimensions[0] if dimensions else None,
            "height": dimensions[1] if dimensions else None,
        }, deep=True)

    @staticmethod
    def _dimension_multiple(template: ComfyUIWorkflowTemplate) -> int:
        value = template.display_metadata.get("dimension_multiple", 1)
        return int(value) if isinstance(value, (int, float)) and int(value) > 0 else 1

    @staticmethod
    def _aspect_value(value: Any) -> float | None:
        text = value.value if hasattr(value, "value") else str(value)
        try:
            left, right = text.split(":", 1)
            return float(left) / float(right)
        except (ValueError, ZeroDivisionError):
            return None

    @staticmethod
    def _is_noop(value: str) -> bool:
        return value.strip().lower() in _NOOP_TOKENS

    @classmethod
    def _canonical_motion(cls, motion: CameraMotionSpec) -> CameraMotionSpec:
        if motion.motion_type != CameraMotionType.STATIC:
            return motion.model_copy(deep=True)
        return motion.model_copy(update={
            "direction": None if motion.direction is None or cls._is_noop(motion.direction)
                         else motion.direction,
            "speed": None if motion.speed is None or cls._is_noop(motion.speed)
                     else motion.speed,
            "path": list(motion.path),
        }, deep=True)

    @staticmethod
    def _stable_seed(request: VideoGenerationRequest) -> int:
        prompt = request.prompt_package
        material = {
            "project_id": request.project_id,
            "scene_id": request.scene_id,
            "shot_id": request.shot_id,
            "generation_revision": request.job_id,
            "prompt": {
                "compiler_id": prompt.compiler_id,
                "compiler_version": prompt.compiler_version,
                "positive_prompt": prompt.positive_prompt,
                "negative_prompt": prompt.negative_prompt,
                "source_shot_id": prompt.source_shot_id,
            },
            "mode": request.mode.value,
            "duration_seconds": float(request.duration_seconds),
            "fps": float(request.fps),
            "delivery_dimensions": [request.width, request.height, str(request.aspect_ratio)],
            "camera_motion": request.camera_motion.model_dump(mode="json"),
            "temporal_control": request.temporal_control.model_dump(mode="json"),
            "provider_parameters": request.provider_parameters,
            "references": sorted(
                (item.artifact_id, item.version, item.reference_type.value)
                for item in request.references
            ),
        }
        encoded = json.dumps(
            material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big") & ((1 << 63) - 1)

    @staticmethod
    def _artifact_fact(artifact: Artifact) -> dict[str, Any]:
        dimensions = VideoGenerationPreflight._reference_dimensions(
            MediaReference(reference_type="style", artifact_id=artifact.artifact_id),
            {artifact.artifact_id: artifact},
        )
        return {
            "artifact_id": artifact.artifact_id,
            "version": artifact.version,
            "dimensions": list(dimensions) if dimensions else None,
            "sha256": artifact.provenance.parameters.get("sha256"),
        }

    @staticmethod
    def _json(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool, list, dict)):
            return value
        if hasattr(value, "value"):
            return value.value
        return str(value)
