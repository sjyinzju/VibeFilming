"""First/last frame planning into typed image-generation requests."""

from __future__ import annotations

from movie_agent.cinematic.frame_planning import RuleBasedFramePlanner
from movie_agent.domain import AspectRatio, ResourceClass, Shot
from movie_agent.media.compilers import GenericImagePromptCompiler, ImagePromptCompiler
from movie_agent.media.contracts import (
    FramePlan,
    ImageGenerationMode,
    ImageGenerationRequest,
    ImagePurpose,
    MediaCapabilityRequirement,
    MediaReference,
    ReferenceType,
)


class MediaFramePlanner:
    def __init__(self, compiler: ImagePromptCompiler | None = None) -> None:
        self.anchor_planner = RuleBasedFramePlanner()
        self.compiler = compiler or GenericImagePromptCompiler()

    def plan(
        self,
        project_id: str,
        shot: Shot,
        *,
        width: int,
        height: int,
        aspect_ratio: AspectRatio | str,
        previous_shot: Shot | None = None,
        previous_last_frame_artifact_id: str | None = None,
    ) -> tuple[FramePlan, object]:
        previous_state = previous_shot.expected_state_after if previous_shot else None
        if previous_state and previous_last_frame_artifact_id:
            previous_state = previous_state.model_copy(
                update={"last_frame_artifact_id": previous_last_frame_artifact_id}, deep=True
            )
        anchors = self.anchor_planner.plan(shot, previous_shot, previous_state)
        previous_reference = None
        if previous_last_frame_artifact_id:
            previous_reference = MediaReference(
                reference_type=ReferenceType.PREVIOUS_FRAME,
                artifact_id=previous_last_frame_artifact_id,
                shot_id=previous_shot.shot_id if previous_shot else None,
            )
        base_references = [
            MediaReference(reference_type=ReferenceType.STYLE, artifact_id=artifact_id)
            for artifact_id in shot.reference_artifact_ids
        ]
        first_references = ([previous_reference] if previous_reference else []) + base_references
        first_mode = (ImageGenerationMode.REFERENCE_TO_IMAGE if first_references
                      else ImageGenerationMode.TEXT_TO_IMAGE)
        first_prompt = self.compiler.compile(shot, ImagePurpose.FIRST_FRAME, first_references)
        first = ImageGenerationRequest(
            job_id=f"frame:{shot.shot_id}:first", project_id=project_id,
            scene_id=shot.scene_id, shot_id=shot.shot_id, prompt_package=first_prompt,
            references=first_references, quality_profile=shot.quality_profile,
            resource_class=ResourceClass.MEDIUM, output_artifact_id=f"frame_{shot.shot_id}_first",
            mode=first_mode, purpose=ImagePurpose.FIRST_FRAME,
            width=width, height=height, aspect_ratio=aspect_ratio,
            required_capabilities=[MediaCapabilityRequirement(
                capability="multi_reference", required=bool(first_references))],
        )
        first_reference = MediaReference(
            reference_type=ReferenceType.FIRST_FRAME,
            artifact_id=first.output_artifact_id,
            shot_id=shot.shot_id,
        )
        last_references = [first_reference, *base_references]
        last_prompt = self.compiler.compile(shot, ImagePurpose.LAST_FRAME, last_references)
        last = ImageGenerationRequest(
            job_id=f"frame:{shot.shot_id}:last", project_id=project_id,
            scene_id=shot.scene_id, shot_id=shot.shot_id, prompt_package=last_prompt,
            references=last_references, quality_profile=shot.quality_profile,
            resource_class=ResourceClass.MEDIUM, output_artifact_id=f"frame_{shot.shot_id}_last",
            mode=ImageGenerationMode.REFERENCE_TO_IMAGE, purpose=ImagePurpose.LAST_FRAME,
            width=width, height=height, aspect_ratio=aspect_ratio,
            required_capabilities=[MediaCapabilityRequirement(capability="multi_reference")],
        )
        return FramePlan(
            shot_id=shot.shot_id, first_frame_request=first,
            last_frame_request=last, previous_last_frame_reference=previous_reference,
        ), anchors
