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
    ReferencePurpose,
    ImageCapabilities,
    MediaDimensions,
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
        references: list[MediaReference] | None = None,
        capabilities: ImageCapabilities | None = None,
    ) -> tuple[FramePlan, object]:
        requested_dimensions = MediaDimensions(width=width, height=height, aspect_ratio=aspect_ratio)
        if capabilities:
            scale = min(1, (capabilities.max_width or width) / width,
                        (capabilities.max_height or height) / height)
            multiple = capabilities.dimension_multiple
            width = int(width * scale) // multiple * multiple
            height = int(height * scale) // multiple * multiple
            if min(width, height) < capabilities.min_dimension:
                raise ValueError("Image canvas aspect ratio cannot fit the provider dimension limits")
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
        legacy_references = [
            MediaReference(reference_type=ReferenceType.STYLE, artifact_id=artifact_id)
            for artifact_id in shot.reference_artifact_ids
        ]
        base_references = list(references or legacy_references)
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
        if capabilities and capabilities.image_to_image and not capabilities.multi_reference:
            # Only explicit uploaded SOURCE_IMAGE or generated continuity frames may
            # become one Img2Img source. Style/identity/multiple references stay in the
            # request and fail through the router/job; none are silently discarded.
            first = self._single_source(first)
            if (first.source_image is not None and len(base_references) == 1
                    and first.source_image.reference_id == base_references[0].reference_id):
                # The uploaded source has already conditioned the first frame; the
                # last frame inherits it through the generated first-frame parent.
                last = last.model_copy(update={"references": [first_reference],
                    "prompt_package": self.compiler.compile(shot, ImagePurpose.LAST_FRAME, [first_reference])})
            last = self._single_source(last)
        return FramePlan(
            shot_id=shot.shot_id, first_frame_request=first,
            last_frame_request=last, previous_last_frame_reference=previous_reference,
            requested_dimensions=requested_dimensions,
        ), anchors

    @staticmethod
    def _single_source(request: ImageGenerationRequest) -> ImageGenerationRequest:
        refs = request.references
        allowed = {ReferenceType.SOURCE_IMAGE, ReferenceType.FIRST_FRAME, ReferenceType.PREVIOUS_FRAME}
        if len(refs) == 1 and refs[0].reference_type in allowed and refs[0].purpose in {None, ReferencePurpose.FIRST_FRAME}:
            return request.model_copy(update={"mode": ImageGenerationMode.IMAGE_TO_IMAGE,
                "source_image": refs[0], "references": [],
                "required_capabilities": [MediaCapabilityRequirement(capability="image_to_image")]})
        if not refs:
            return request.model_copy(update={"required_capabilities": [MediaCapabilityRequirement(capability="text_to_image")]})
        return request
