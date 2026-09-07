"""The built-in H3 profile is the exact, validated real workflow contract."""

from __future__ import annotations

import pytest

from movie_agent.comfyui import (
    ComfyUIInputAsset,
    ComfyUIOutputSource,
    ComfyUIWorkflowCompiler,
    ComfyUIWorkflowRegistry,
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


def _object_info(template):
    result = {}
    for node in template.api_workflow.values():
        required = {name: ["STRING", {}] for name in node.inputs}
        if node.class_type == "UNETLoader":
            required["unet_name"] = [[node.inputs["unet_name"]], {}]
        elif node.class_type == "CLIPLoader":
            required["clip_name"] = [[node.inputs["clip_name"]], {}]
        elif node.class_type == "VAELoader":
            required["vae_name"] = [[
                "minimax_h3_video_vae_fp16.safetensors",
                "minimax_h3_audio_vae_fp32.safetensors",
            ], {}]
        result[node.class_type] = {"input": {"required": required, "optional": {}}}
    return result


def test_builtin_h3_template_and_manifest_match_the_verified_api_graph() -> None:
    profile, template, manifest = ComfyUIWorkflowRegistry().resolve("minimax_h3_fl2va")

    validate_template_binding(template, manifest, _object_info(template))
    assert template.template_hash == "075cf2ed8d16b5c135abd75f6555ffb0aa56c42736e41bc3f8728c13a541afa8"
    assert len(template.api_workflow) == 18
    assert template.api_workflow["105:104"].inputs["first_frame"] == ["114", 0]
    assert template.api_workflow["105:104"].inputs["last_frame"] == ["114:last_frame", 0]
    assert template.api_workflow["105:91"].inputs["audio"] == ["105:23", 0]
    assert template.outputs[0].source == ComfyUIOutputSource.REMOTE_FILE
    assert template.outputs[1].source == ComfyUIOutputSource.MUXED_AUDIO
    assert template.display_metadata["source_revision"]
    assert profile.template_id == template.template_id


def test_h3_compiler_binds_only_real_inputs_and_inlines_negative_guidance() -> None:
    profile, template, manifest = ComfyUIWorkflowRegistry().resolve("minimax_h3_fl2va")
    request = VideoGenerationRequest(
        job_id="job_h3", project_id="project_h3", scene_id="scene_h3", shot_id="shot_h3",
        output_artifact_id="video_h3",
        prompt_package=PromptPackage(
            compiler_id="generic-video", compiler_version="1.0.0",
            positive_prompt="A woman reaches for a teal kettle.",
            negative_prompt="identity drift",
        ),
        mode=VideoGenerationMode.FIRST_LAST_FRAME_TO_VIDEO,
        duration_seconds=1, fps=24, width=608, height=352, aspect_ratio="16:9", seed=42,
        camera_motion=CameraMotionSpec(motion_type="static"),
    )
    assets = [
        ComfyUIInputAsset(
            semantic_slot=slot, artifact_id=slot, artifact_version=1,
            sha256=digest * 64, filename=f"{slot}.png", subfolder="movie-agent",
        )
        for slot, digest in (("first_frame", "a"), ("last_frame", "b"))
    ]
    capabilities = ProviderCapabilities(
        provider_id="comfyui-video", kind=ProviderKind.VIDEO,
        modalities=[MediaModality.VIDEO], tasks=["video"],
        video=VideoCapabilities(
            first_frame=True, last_frame=True, first_last_frame=True,
            audio_generation=True, max_duration_seconds=15,
            max_width=1344, max_height=1344, supported_fps=[24],
        ),
        resource_profiles=[ResourceProfile(resource_class=ResourceClass.MEDIUM)],
    )
    strategy = MediaGenerationStrategy(
        strategy_type=GenerationStrategyType.FIRST_LAST_FRAME_TO_VIDEO,
        reason="verified H3 path",
        required_capabilities=["first_frame", "last_frame", "first_last_frame"],
    )

    spec = ComfyUIWorkflowCompiler().compile(
        request=request, strategy=strategy, capabilities=capabilities,
        input_assets=assets, template=template, binding_manifest=manifest,
        model_profile=profile.model_profile,
    )

    h3 = spec.prompt["105:104"]["inputs"]
    assert h3["prompt"] == "A woman reaches for a teal kettle.\nAvoid: identity drift"
    assert (h3["width"], h3["height"]) == (608, 352)
    assert h3["first_frame"] == ["114", 0]
    assert h3["last_frame"] == ["114:last_frame", 0]
    assert spec.prompt["114"]["inputs"]["image"] == "movie-agent/first_frame.png"
    assert spec.prompt["114:last_frame"]["inputs"]["image"] == "movie-agent/last_frame.png"
    assert spec.prompt["105:15"]["inputs"]["noise_seed"] == 42
    assert spec.prompt["105:91"]["inputs"]["fps"] == 24
    assert spec.prompt["105:111"]["inputs"]["value"] == 1

    for unsupported_request in (
        request.model_copy(update={
            "camera_motion": CameraMotionSpec(motion_type="pan", direction="left"),
        }),
        request.model_copy(update={
            "temporal_control": request.temporal_control.model_copy(update={
                "preserve_identity": False,
            }),
        }),
        request.model_copy(update={"provider_parameters": {"unknown": 1}}),
    ):
        with pytest.raises(ProviderFailure) as captured:
            ComfyUIWorkflowCompiler().compile(
                request=unsupported_request, strategy=strategy, capabilities=capabilities,
                input_assets=assets, template=template, binding_manifest=manifest,
                model_profile=profile.model_profile,
            )
        assert captured.value.error_type.value == "unsupported_capability"
