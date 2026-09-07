"""Golden behavior for the model-neutral ComfyUI workflow compiler."""

from __future__ import annotations

import json
from pathlib import Path

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
from movie_agent.comfyui import (
    BindingValueType,
    ComfyUIInputAsset,
    ComfyUIOutputDeclaration,
    ComfyUIWorkflowCompiler,
    ComfyUIWorkflowTemplate,
    WorkflowBinding,
    WorkflowBindingManifest,
    compute_workflow_hash,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_fixed_request_compiles_to_exact_api_format_prompt() -> None:
    workflow = json.loads((FIXTURES / "comfyui_video_foundation_api.json").read_text("utf-8"))
    template = ComfyUIWorkflowTemplate(
        template_id="comfyui_video_foundation_fixture",
        version="1.0.0",
        modality=MediaModality.VIDEO,
        generation_mode=VideoGenerationMode.FIRST_LAST_FRAME_TO_VIDEO,
        api_workflow=workflow,
        template_hash=compute_workflow_hash(workflow),
        supported_capabilities=["first_frame", "last_frame", "first_last_frame", "audio_generation"],
        required_semantic_slots=[
            "prompt", "negative_prompt", "seed", "width", "height", "fps",
            "duration_seconds", "frame_count", "first_frame", "last_frame",
        ],
        outputs=[
            ComfyUIOutputDeclaration(
                node_id="20", modality=MediaModality.VIDEO, purpose="shot_video",
                history_key="videos", mime_type="video/mp4", extension="mp4", primary=True,
            ),
            ComfyUIOutputDeclaration(
                node_id="21", modality=MediaModality.AUDIO, purpose="native_audio",
                history_key="audio", mime_type="audio/wav", extension="wav",
            ),
        ],
    )
    bindings = WorkflowBindingManifest(
        manifest_id="comfyui_video_foundation_fixture_bindings",
        version="1.0.0",
        template_id=template.template_id,
        template_version=template.version,
        bindings=[
            WorkflowBinding(semantic_slot="prompt", node_id="10", input_name="prompt", value_type=BindingValueType.STRING, required=True),
            WorkflowBinding(semantic_slot="negative_prompt", node_id="10", input_name="negative_prompt", value_type=BindingValueType.STRING, required=True),
            WorkflowBinding(semantic_slot="seed", node_id="10", input_name="seed", value_type=BindingValueType.INTEGER, required=True),
            WorkflowBinding(semantic_slot="width", node_id="10", input_name="width", value_type=BindingValueType.INTEGER, required=True),
            WorkflowBinding(semantic_slot="height", node_id="10", input_name="height", value_type=BindingValueType.INTEGER, required=True),
            WorkflowBinding(semantic_slot="fps", node_id="10", input_name="fps", value_type=BindingValueType.NUMBER, required=True),
            WorkflowBinding(semantic_slot="duration_seconds", node_id="10", input_name="duration_seconds", value_type=BindingValueType.NUMBER, required=True),
            WorkflowBinding(semantic_slot="frame_count", node_id="10", input_name="frame_count", value_type=BindingValueType.INTEGER, required=True),
            WorkflowBinding(semantic_slot="first_frame", node_id="10", input_name="first_frame", value_type=BindingValueType.ASSET, required=True),
            WorkflowBinding(semantic_slot="last_frame", node_id="10", input_name="last_frame", value_type=BindingValueType.ASSET, required=True),
        ],
    )
    request = VideoGenerationRequest(
        job_id="job_compiler", project_id="project_compiler", scene_id="scene_compiler",
        shot_id="shot_compiler", output_artifact_id="video_compiler",
        prompt_package=PromptPackage(
            compiler_id="generic_video", compiler_version="1.0.0",
            positive_prompt="A locked-off cinematic test shot",
            negative_prompt="flicker, warped hands",
        ),
        mode=VideoGenerationMode.FIRST_LAST_FRAME_TO_VIDEO,
        duration_seconds=2.5, fps=24, width=1280, height=720, aspect_ratio="16:9",
        seed=42, camera_motion=CameraMotionSpec(motion_type="static"),
    )
    strategy = MediaGenerationStrategy(
        strategy_type=GenerationStrategyType.FIRST_LAST_FRAME_TO_VIDEO,
        reason="golden fixture", required_capabilities=["first_frame", "last_frame"],
    )
    capabilities = ProviderCapabilities(
        provider_id="comfyui-video", kind=ProviderKind.VIDEO,
        modalities=[MediaModality.VIDEO], tasks=["video"],
        video=VideoCapabilities(
            first_frame=True, last_frame=True, first_last_frame=True, audio_generation=True,
            max_duration_seconds=10, max_width=1920, max_height=1080, supported_fps=[24],
        ),
        resource_profiles=[ResourceProfile(resource_class=ResourceClass.MEDIUM)],
    )
    assets = [
        ComfyUIInputAsset(
            semantic_slot="first_frame", artifact_id="first", artifact_version=3,
            sha256="1" * 64, filename="first_v3_111111111111.png",
            subfolder="movie-agent", type="input",
        ),
        ComfyUIInputAsset(
            semantic_slot="last_frame", artifact_id="last", artifact_version=2,
            sha256="2" * 64, filename="last_v2_222222222222.png",
            subfolder="movie-agent", type="input",
        ),
    ]

    spec = ComfyUIWorkflowCompiler().compile(
        request=request,
        strategy=strategy,
        capabilities=capabilities,
        input_assets=assets,
        template=template,
        binding_manifest=bindings,
        model_profile="foundation-test-only",
    )

    expected = json.loads((FIXTURES / "comfyui_video_foundation_golden.json").read_text("utf-8"))
    assert spec.prompt == expected
    assert json.loads(spec.prompt_json) == expected
    assert spec.template_hash == template.template_hash
    assert spec.template_id == template.template_id and spec.template_version == "1.0.0"
    assert spec.binding_manifest_id == bindings.manifest_id and spec.binding_manifest_version == "1.0.0"
    assert [item.artifact_version for item in spec.input_assets] == [3, 2]
