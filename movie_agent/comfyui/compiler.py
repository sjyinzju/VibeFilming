"""Deterministic semantic binding and validation for ComfyUI workflows."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from movie_agent.domain import ProviderErrorType
from movie_agent.media.contracts import (
    MediaGenerationStrategy,
    MediaModality,
    ProviderCapabilities,
    VideoGenerationRequest,
)
from movie_agent.providers.base import ProviderFailure

from .contracts import (
    BindingTransform,
    BindingValueType,
    ComfyUIExecutionSpec,
    ComfyUIInputAsset,
    ComfyUIWorkflowTemplate,
    WorkflowBinding,
    WorkflowBindingManifest,
    camera_motion_is_prompt_encoded,
)


class ComfyUIWorkflowValidationError(ValueError):
    pass


def validate_template_binding(
    template: ComfyUIWorkflowTemplate,
    manifest: WorkflowBindingManifest,
    object_info: dict[str, Any] | None = None,
) -> None:
    if (manifest.template_id, manifest.template_version) != (template.template_id, template.version):
        raise ComfyUIWorkflowValidationError("binding manifest targets a different template version")
    bound_slots = {item.semantic_slot for item in manifest.bindings}
    missing_slots = [item for item in template.required_semantic_slots if item not in bound_slots]
    if missing_slots:
        raise ComfyUIWorkflowValidationError(
            f"required semantic slot is not bound: {missing_slots[0]}"
        )
    if object_info is not None:
        for node in template.api_workflow.values():
            if node.class_type not in object_info:
                raise ComfyUIWorkflowValidationError(
                    f"ComfyUI node class is unavailable: {node.class_type}"
                )
            node_schema = object_info[node.class_type]
            schema_inputs = node_schema.get("input", {})
            known = {**schema_inputs.get("required", {}), **schema_inputs.get("optional", {})}
            for input_name, value in node.inputs.items():
                declaration = known.get(input_name)
                if (
                    input_name in {"unet_name", "clip_name", "vae_name"}
                    and isinstance(value, str)
                    and isinstance(declaration, (list, tuple))
                    and declaration
                    and isinstance(declaration[0], list)
                    and value not in declaration[0]
                ):
                    raise ComfyUIWorkflowValidationError(
                        f"ComfyUI model is unavailable: {node.class_type}.{input_name}={value}"
                    )
    for binding in manifest.bindings:
        node = template.api_workflow.get(binding.node_id)
        if node is None:
            raise ComfyUIWorkflowValidationError(
                f"binding node does not exist: {binding.node_id}"
            )
        if binding.input_name not in node.inputs:
            raise ComfyUIWorkflowValidationError(
                f"binding input does not exist: {binding.node_id}.{binding.input_name}"
            )
        if binding.expected_class_type and node.class_type != binding.expected_class_type:
            raise ComfyUIWorkflowValidationError(
                f"binding class_type mismatch for node {binding.node_id}"
            )
        if object_info is not None:
            node_schema = object_info.get(node.class_type)
            inputs = node_schema.get("input", {})
            known_inputs = set(inputs.get("required", {})) | set(inputs.get("optional", {}))
            if binding.input_name not in known_inputs:
                raise ComfyUIWorkflowValidationError(
                    f"ComfyUI node input is unavailable: {node.class_type}.{binding.input_name}"
                )


class ComfyUIWorkflowCompiler:
    """Compile domain semantics into one validated API-format prompt."""

    def compile(
        self,
        *,
        request: VideoGenerationRequest,
        strategy: MediaGenerationStrategy,
        capabilities: ProviderCapabilities,
        input_assets: Iterable[ComfyUIInputAsset],
        template: ComfyUIWorkflowTemplate,
        binding_manifest: WorkflowBindingManifest,
        model_profile: str,
    ) -> ComfyUIExecutionSpec:
        validate_template_binding(template, binding_manifest)
        if request.mode != template.generation_mode:
            raise ProviderFailure(
                "selected ComfyUI workflow does not support the request mode",
                ProviderErrorType.UNSUPPORTED_CAPABILITY,
            )
        self._validate_capabilities(request, strategy, capabilities, template)
        assets = list(input_assets)
        bound_slots = {item.semantic_slot for item in binding_manifest.bindings}
        unbound_assets = sorted({item.semantic_slot for item in assets} - bound_slots)
        if unbound_assets:
            raise ProviderFailure(
                f"ComfyUI workflow does not bind input asset slot: {unbound_assets[0]}",
                ProviderErrorType.UNSUPPORTED_CAPABILITY,
            )
        slots = self._semantic_values(
            request, assets, inline_negative_prompt=binding_manifest.inline_negative_prompt
        )
        if "camera_motion" not in bound_slots and (
            request.camera_motion.motion_type.value != "static"
            or request.camera_motion.direction is not None
            or request.camera_motion.speed is not None
            or request.camera_motion.path
        ) and not camera_motion_is_prompt_encoded(request, binding_manifest):
            raise ProviderFailure(
                "ComfyUI workflow does not support structured camera motion",
                ProviderErrorType.UNSUPPORTED_CAPABILITY,
            )
        if (
            "temporal_control" not in bound_slots
            and request.temporal_control.model_dump()
            != type(request.temporal_control)().model_dump()
        ):
            raise ProviderFailure(
                "ComfyUI workflow does not support custom temporal control",
                ProviderErrorType.UNSUPPORTED_CAPABILITY,
            )
        declared_parameters = {
            item.semantic_slot.removeprefix("provider_parameters.")
            for item in binding_manifest.bindings
            if item.semantic_slot.startswith("provider_parameters.")
        }
        unknown_parameters = set(request.provider_parameters) - declared_parameters
        if unknown_parameters:
            raise ProviderFailure(
                f"ComfyUI workflow does not declare provider parameter: {sorted(unknown_parameters)[0]}",
                ProviderErrorType.UNSUPPORTED_CAPABILITY,
            )
        if (
            request.prompt_package.negative_prompt
            and "negative_prompt" not in bound_slots
            and not binding_manifest.inline_negative_prompt
        ):
            raise ProviderFailure(
                "ComfyUI workflow does not support a separate negative prompt",
                ProviderErrorType.UNSUPPORTED_CAPABILITY,
            )
        prompt = {
            node_id: node.model_dump(mode="json", by_alias=True, exclude_defaults=True)
            for node_id, node in template.api_workflow.items()
        }
        for binding in binding_manifest.bindings:
            value = slots.get(binding.semantic_slot)
            if value is None:
                if binding.required:
                    raise ComfyUIWorkflowValidationError(
                        f"required semantic value is missing: {binding.semantic_slot}"
                    )
                continue
            value = self._transform(value, binding)
            self._validate_value_type(value, binding)
            prompt[binding.node_id]["inputs"][binding.input_name] = value
        return ComfyUIExecutionSpec(
            prompt=prompt,
            input_assets=assets,
            expected_outputs=template.outputs,
            template_id=template.template_id,
            template_version=template.version,
            template_hash=template.template_hash,
            binding_manifest_id=binding_manifest.manifest_id,
            binding_manifest_version=binding_manifest.version,
            model_profile=model_profile,
            execution_metadata={
                "request_id": request.request_id,
                "generation_mode": request.mode.value,
                "generation_strategy": strategy.strategy_type.value,
                "frame_count": round(float(request.duration_seconds) * float(request.fps)),
            },
        )

    @staticmethod
    def _semantic_values(
        request: VideoGenerationRequest,
        assets: list[ComfyUIInputAsset],
        *,
        inline_negative_prompt: bool = False,
    ) -> dict[str, Any]:
        by_slot: dict[str, list[ComfyUIInputAsset]] = {}
        for asset in assets:
            by_slot.setdefault(asset.semantic_slot, []).append(asset)
        prompt = request.prompt_package.positive_prompt
        if inline_negative_prompt and request.prompt_package.negative_prompt:
            prompt = f"{prompt}\nAvoid: {request.prompt_package.negative_prompt}"
        values: dict[str, Any] = {
            "prompt": prompt,
            "negative_prompt": request.prompt_package.negative_prompt,
            "seed": request.seed,
            "width": request.width,
            "height": request.height,
            "fps": float(request.fps),
            "duration_seconds": float(request.duration_seconds),
            "frame_count": round(float(request.duration_seconds) * float(request.fps)),
            "generation_mode": request.mode.value,
            "camera_motion": request.camera_motion.model_dump(mode="json"),
            "temporal_control": request.temporal_control.model_dump(mode="json"),
            "start_state": request.start_state.model_dump(mode="json"),
            "end_state": request.end_state.model_dump(mode="json"),
        }
        for slot, items in by_slot.items():
            values[slot] = items[0].input_name if len(items) == 1 else [item.input_name for item in items]
        values.update({f"provider_parameters.{key}": value for key, value in request.provider_parameters.items()})
        return values

    @staticmethod
    def _validate_capabilities(
        request: VideoGenerationRequest,
        strategy: MediaGenerationStrategy,
        capabilities: ProviderCapabilities,
        template: ComfyUIWorkflowTemplate,
    ) -> None:
        video = capabilities.video
        flags = ({name for name, value in video.model_dump().items() if value is True}
                 if video is not None else set())
        effective = flags & set(template.supported_capabilities)
        required = {
            *strategy.required_capabilities,
            *(item.capability for item in request.required_capabilities if item.required),
            *template.required_capabilities,
        }
        if any(
            output.required and output.modality == MediaModality.AUDIO
            for output in template.outputs
        ):
            required.add("audio_generation")
        missing = sorted(required - effective)
        if missing:
            raise ProviderFailure(
                f"ComfyUI workflow lacks required capability: {missing[0]}",
                ProviderErrorType.UNSUPPORTED_CAPABILITY,
            )
        if video is not None:
            if video.max_duration_seconds and request.duration_seconds > video.max_duration_seconds:
                raise ProviderFailure("video duration exceeds ComfyUI workflow limit", ProviderErrorType.UNSUPPORTED_CAPABILITY)
            if video.max_width and request.width > video.max_width:
                raise ProviderFailure("video width exceeds ComfyUI workflow limit", ProviderErrorType.UNSUPPORTED_CAPABILITY)
            if video.max_height and request.height > video.max_height:
                raise ProviderFailure("video height exceeds ComfyUI workflow limit", ProviderErrorType.UNSUPPORTED_CAPABILITY)
            if video.supported_fps and request.fps not in video.supported_fps:
                raise ProviderFailure("video fps is unsupported by ComfyUI workflow", ProviderErrorType.UNSUPPORTED_CAPABILITY)

    @staticmethod
    def _transform(value: Any, binding: WorkflowBinding) -> Any:
        if binding.transform == BindingTransform.STRING:
            return str(value)
        if binding.transform == BindingTransform.INTEGER:
            return int(round(float(value)))
        if binding.transform == BindingTransform.NUMBER:
            return float(value)
        return value

    @staticmethod
    def _validate_value_type(value: Any, binding: WorkflowBinding) -> None:
        expected = binding.value_type
        valid = {
            BindingValueType.STRING: isinstance(value, str),
            BindingValueType.INTEGER: isinstance(value, int) and not isinstance(value, bool),
            BindingValueType.NUMBER: isinstance(value, (int, float)) and not isinstance(value, bool),
            BindingValueType.BOOLEAN: isinstance(value, bool),
            BindingValueType.ASSET: isinstance(value, str),
            BindingValueType.ASSET_LIST: isinstance(value, list) and all(isinstance(item, str) for item in value),
            BindingValueType.JSON: True,
        }[expected]
        if not valid:
            raise ComfyUIWorkflowValidationError(
                f"semantic value type mismatch for {binding.semantic_slot}: expected {expected.value}"
            )
