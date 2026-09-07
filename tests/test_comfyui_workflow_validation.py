"""Fail-fast validation behavior for versioned ComfyUI workflow profiles."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from movie_agent.comfyui import (
    BindingValueType,
    ComfyUIOutputDeclaration,
    ComfyUIInputAsset,
    ComfyUIWorkflowTemplate,
    ComfyUIWorkflowValidationError,
    ComfyUIWorkflowCompiler,
    ComfyUIWorkflowProfile,
    ComfyUIWorkflowRegistry,
    WorkflowBinding,
    WorkflowBindingManifest,
    compute_workflow_hash,
    validate_template_binding,
)
from movie_agent.domain import GenerationStrategyType, PromptPackage, ProviderKind, ResourceClass
from movie_agent.media import (
    CameraMotionSpec,
    MediaGenerationStrategy,
    MediaModality,
    ProviderCapabilities,
    ResourceProfile,
    VideoCapabilities,
    VideoGenerationMode,
    VideoGenerationRequest,
)
from movie_agent.providers import ProviderFailure


def minimal_template_and_manifest():
    workflow = {
        "1": {"class_type": "InputNode", "inputs": {"prompt": "placeholder"}},
        "2": {"class_type": "OutputNode", "inputs": {"source": ["1", 0]}},
    }
    template = ComfyUIWorkflowTemplate(
        template_id="validation_fixture", version="1.0.0",
        modality=MediaModality.VIDEO,
        generation_mode=VideoGenerationMode.TEXT_TO_VIDEO,
        api_workflow=workflow, template_hash=compute_workflow_hash(workflow),
        required_semantic_slots=["prompt"],
        outputs=[ComfyUIOutputDeclaration(
            node_id="2", modality=MediaModality.VIDEO, purpose="video",
            history_key="videos", mime_type="video/mp4", extension="mp4", primary=True,
        )],
    )
    manifest = WorkflowBindingManifest(
        manifest_id="validation_bindings", version="1.0.0",
        template_id=template.template_id, template_version=template.version,
        bindings=[WorkflowBinding(
            semantic_slot="prompt", node_id="1", input_name="prompt",
            value_type=BindingValueType.STRING, required=True,
        )],
    )
    return template, manifest


def test_object_info_validation_requires_every_template_node_class() -> None:
    template, manifest = minimal_template_and_manifest()
    object_info = {
        "InputNode": {"input": {"required": {"prompt": ["STRING", {}]}}},
    }

    with pytest.raises(ComfyUIWorkflowValidationError, match="OutputNode"):
        validate_template_binding(template, manifest, object_info)


def test_template_hash_and_binding_version_node_input_are_fail_fast() -> None:
    template, manifest = minimal_template_and_manifest()
    with pytest.raises(ValidationError, match="hash"):
        ComfyUIWorkflowTemplate(**{
            **template.model_dump(),
            "template_hash": "0" * 64,
        })
    with pytest.raises(ComfyUIWorkflowValidationError, match="different template version"):
        validate_template_binding(
            template, manifest.model_copy(update={"template_version": "2.0.0"})
        )
    with pytest.raises(ComfyUIWorkflowValidationError, match="node does not exist"):
        validate_template_binding(
            template,
            manifest.model_copy(update={"bindings": [
                manifest.bindings[0].model_copy(update={"node_id": "missing"})
            ]}),
        )
    with pytest.raises(ComfyUIWorkflowValidationError, match="input does not exist"):
        validate_template_binding(
            template,
            manifest.model_copy(update={"bindings": [
                manifest.bindings[0].model_copy(update={"input_name": "missing"})
            ]}),
        )
    with pytest.raises(ValidationError, match="conflicting"):
        WorkflowBindingManifest(
            manifest_id="conflict", version="1.0.0",
            template_id=template.template_id, template_version=template.version,
            bindings=[
                manifest.bindings[0],
                manifest.bindings[0].model_copy(update={"semantic_slot": "negative_prompt"}),
            ],
        )


def test_optional_binding_keeps_template_default_and_unknown_parameter_is_rejected() -> None:
    workflow = {
        "1": {"class_type": "InputNode", "inputs": {"prompt": "placeholder", "seed": 7}},
        "2": {"class_type": "OutputNode", "inputs": {"source": ["1", 0]}},
    }
    template = ComfyUIWorkflowTemplate(
        template_id="optional_fixture", version="1.0.0",
        modality=MediaModality.VIDEO, generation_mode=VideoGenerationMode.TEXT_TO_VIDEO,
        api_workflow=workflow, template_hash=compute_workflow_hash(workflow),
        supported_capabilities=["text_to_video"], required_semantic_slots=["prompt"],
        outputs=[ComfyUIOutputDeclaration(
            node_id="2", modality=MediaModality.VIDEO, purpose="video", history_key="videos",
            mime_type="video/mp4", extension="mp4", primary=True,
        )],
    )
    manifest = WorkflowBindingManifest(
        manifest_id="optional_bindings", version="1.0.0",
        template_id=template.template_id, template_version=template.version,
        bindings=[
            WorkflowBinding(semantic_slot="prompt", node_id="1", input_name="prompt", value_type=BindingValueType.STRING, required=True),
            WorkflowBinding(semantic_slot="seed", node_id="1", input_name="seed", value_type=BindingValueType.INTEGER, required=False),
        ],
    )
    request = VideoGenerationRequest(
        job_id="optional_job", project_id="optional_project", scene_id="optional_scene",
        shot_id="optional_shot", output_artifact_id="optional_video",
        prompt_package=PromptPackage(
            compiler_id="generic", compiler_version="1.0.0", positive_prompt="bound prompt"
        ),
        mode=VideoGenerationMode.TEXT_TO_VIDEO, duration_seconds=1, fps=24,
        width=640, height=360, aspect_ratio="16:9",
        camera_motion=CameraMotionSpec(motion_type="static"),
    )
    strategy = MediaGenerationStrategy(
        strategy_type=GenerationStrategyType.TEXT_TO_VIDEO, reason="validation"
    )
    capabilities = ProviderCapabilities(
        provider_id="comfyui-video", kind=ProviderKind.VIDEO,
        modalities=[MediaModality.VIDEO], tasks=["video"],
        video=VideoCapabilities(text_to_video=True),
        resource_profiles=[ResourceProfile(resource_class=ResourceClass.MEDIUM)],
    )
    compiler = ComfyUIWorkflowCompiler()
    spec = compiler.compile(
        request=request, strategy=strategy, capabilities=capabilities,
        input_assets=[], template=template, binding_manifest=manifest,
        model_profile="validation",
    )
    assert spec.prompt["1"]["inputs"] == {"prompt": "bound prompt", "seed": 7}

    with pytest.raises(ProviderFailure) as captured:
        compiler.compile(
            request=request.model_copy(update={"provider_parameters": {"undeclared": 1}}),
            strategy=strategy, capabilities=capabilities, input_assets=[],
            template=template, binding_manifest=manifest, model_profile="validation",
        )
    assert captured.value.error_type.value == "unsupported_capability"

    with pytest.raises(ProviderFailure) as captured_asset:
        compiler.compile(
            request=request, strategy=strategy, capabilities=capabilities,
            input_assets=[ComfyUIInputAsset(
                semantic_slot="reference_images", artifact_id="style", artifact_version=1,
                sha256="a" * 64, filename="style.png", subfolder="movie-agent",
            )],
            template=template, binding_manifest=manifest, model_profile="validation",
        )
    assert captured_asset.value.error_type.value == "unsupported_capability"


def test_registry_prevents_silent_mutation_of_registered_template_versions() -> None:
    template, manifest = minimal_template_and_manifest()
    registry = ComfyUIWorkflowRegistry()
    registry.register_template(template)
    registry.register_manifest(manifest)
    registry.register_profile(ComfyUIWorkflowProfile(
        profile_id="validation", template_id=template.template_id,
        template_version=template.version, binding_manifest_id=manifest.manifest_id,
        binding_manifest_version=manifest.version, model_profile="validation",
    ))

    template.api_workflow["1"].inputs["prompt"] = "mutated outside registry"
    _, first_read, _ = registry.resolve("validation")
    assert first_read.api_workflow["1"].inputs["prompt"] == "placeholder"
    first_read.api_workflow["1"].inputs["prompt"] = "mutated returned copy"
    _, second_read, _ = registry.resolve("validation")
    assert second_read.api_workflow["1"].inputs["prompt"] == "placeholder"
