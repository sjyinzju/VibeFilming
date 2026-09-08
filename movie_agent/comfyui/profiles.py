"""Built-in, verified ComfyUI workflow profiles."""

from __future__ import annotations

import json
from pathlib import Path

from movie_agent.media.contracts import MediaModality, VideoGenerationMode

from .contracts import (
    BindingValueType,
    ComfyUIOutputDeclaration,
    ComfyUIOutputSource,
    ComfyUIWorkflowProfile,
    ComfyUIWorkflowTemplate,
    WorkflowBinding,
    WorkflowBindingManifest,
    compute_workflow_hash,
)


MINIMAX_H3_FL2VA_PROFILE_ID = "minimax_h3_fl2va"
MINIMAX_H3_FL2VA_TEMPLATE_ID = "comfy_org_minimax_h3_fl2va"
MINIMAX_H3_FL2VA_TEMPLATE_VERSION = "1.0.0"
MINIMAX_H3_FL2VA_BINDING_ID = "comfy_org_minimax_h3_fl2va_bindings"
MINIMAX_H3_FL2VA_BINDING_VERSION = "1.1.0"
MINIMAX_H3_FL2VA_MODEL_PROFILE = "minimax-h3-fl2va-int8-convrot"
MINIMAX_H3_OFFICIAL_SOURCE_COMMIT = "f9f1d1014d98d8cad87e5c3aaaf388f7f9240d27"


def minimax_h3_fl2va_components() -> tuple[
    ComfyUIWorkflowProfile, ComfyUIWorkflowTemplate, WorkflowBindingManifest
]:
    workflow_path = Path(__file__).with_name("workflows") / "minimax_h3_fl2va_api_v1.json"
    workflow = json.loads(workflow_path.read_text("utf-8"))
    template = ComfyUIWorkflowTemplate(
        template_id=MINIMAX_H3_FL2VA_TEMPLATE_ID,
        version=MINIMAX_H3_FL2VA_TEMPLATE_VERSION,
        modality=MediaModality.VIDEO,
        generation_mode=VideoGenerationMode.FIRST_LAST_FRAME_TO_VIDEO,
        api_workflow=workflow,
        template_hash=compute_workflow_hash(workflow),
        required_capabilities=["first_frame", "last_frame", "first_last_frame", "audio_generation"],
        supported_capabilities=["first_frame", "last_frame", "first_last_frame", "audio_generation"],
        required_semantic_slots=[
            "prompt", "seed", "width", "height", "fps", "duration_seconds",
            "first_frame", "last_frame",
        ],
        outputs=[
            ComfyUIOutputDeclaration(
                node_id="92", modality=MediaModality.VIDEO, purpose="shot_video",
                history_key="images", mime_type="video/mp4", extension="mp4",
                codec="h264", primary=True,
            ),
            ComfyUIOutputDeclaration(
                node_id="92", modality=MediaModality.AUDIO, purpose="generated_native_audio",
                history_key="images", mime_type="audio/mp4", extension="m4a",
                codec="aac", source=ComfyUIOutputSource.MUXED_AUDIO,
            ),
        ],
        display_metadata={
            "display_name": "MiniMax H3 FL2VA + Native Audio",
            "source": "Comfy-Org/workflow_templates",
            "source_revision": MINIMAX_H3_OFFICIAL_SOURCE_COMMIT,
            "source_path": "templates/video_minimax_h3_i2v.json",
            "adaptation": "Official API export with the documented last_frame input connected to a second LoadImage node.",
            "dimension_multiple": 32,
        },
    )
    # Node display metadata is derived from the immutable API graph without importing GUI coordinates.
    template = template.model_copy(update={
        "node_metadata": {
            node_id: {
                "display_label": node.meta.get("title", node.class_type),
                "category": (
                    "input" if node.class_type == "LoadImage" else
                    "model" if node.class_type in {"UNETLoader", "CLIPLoader", "VAELoader"} else
                    "conditioning" if node.class_type in {"MiniMaxH3ImageToVideo", "BasicGuider"} else
                    "sampling" if node.class_type in {"RandomNoise", "KSamplerSelect", "BasicScheduler", "SamplerCustomAdvanced"} else
                    "decode" if node.class_type in {"VAEDecode", "VAEDecodeAudio"} else
                    "output" if node.class_type in {"CreateVideo", "SaveVideo"} else
                    "control"
                ),
            }
            for node_id, node in template.api_workflow.items()
        }
    })
    manifest = WorkflowBindingManifest(
        manifest_id=MINIMAX_H3_FL2VA_BINDING_ID,
        version=MINIMAX_H3_FL2VA_BINDING_VERSION,
        template_id=template.template_id,
        template_version=template.version,
        inline_negative_prompt=True,
        camera_motion_in_prompt=True,
        bindings=[
            WorkflowBinding(semantic_slot="prompt", node_id="105:104", input_name="prompt", value_type=BindingValueType.STRING, required=True, expected_class_type="MiniMaxH3ImageToVideo"),
            WorkflowBinding(semantic_slot="seed", node_id="105:15", input_name="noise_seed", value_type=BindingValueType.INTEGER, required=True, expected_class_type="RandomNoise"),
            WorkflowBinding(semantic_slot="width", node_id="105:104", input_name="width", value_type=BindingValueType.INTEGER, required=True, expected_class_type="MiniMaxH3ImageToVideo"),
            WorkflowBinding(semantic_slot="height", node_id="105:104", input_name="height", value_type=BindingValueType.INTEGER, required=True, expected_class_type="MiniMaxH3ImageToVideo"),
            WorkflowBinding(semantic_slot="fps", node_id="105:91", input_name="fps", value_type=BindingValueType.NUMBER, required=True, expected_class_type="CreateVideo"),
            WorkflowBinding(semantic_slot="duration_seconds", node_id="105:111", input_name="value", value_type=BindingValueType.NUMBER, required=True, expected_class_type="PrimitiveFloat"),
            WorkflowBinding(semantic_slot="first_frame", node_id="114", input_name="image", value_type=BindingValueType.ASSET, required=True, expected_class_type="LoadImage"),
            WorkflowBinding(semantic_slot="last_frame", node_id="114:last_frame", input_name="image", value_type=BindingValueType.ASSET, required=True, expected_class_type="LoadImage"),
        ],
    )
    profile = ComfyUIWorkflowProfile(
        profile_id=MINIMAX_H3_FL2VA_PROFILE_ID,
        template_id=template.template_id,
        template_version=template.version,
        binding_manifest_id=manifest.manifest_id,
        binding_manifest_version=manifest.version,
        model_profile=MINIMAX_H3_FL2VA_MODEL_PROFILE,
    )
    return profile, template, manifest
