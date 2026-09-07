"""Rule-based media generation strategy independent of concrete model names."""

from __future__ import annotations

from movie_agent.domain import AnchorKind, GenerationStrategyType, Shot
from movie_agent.media.contracts import MediaGenerationStrategy, ProviderCapabilities


class GenerationStrategyPlanner:
    """Choose the strongest provider-neutral strategy supported by the capability snapshot."""

    def plan(
        self,
        shot: Shot,
        capabilities: list[ProviderCapabilities],
        references: list | None = None,
    ) -> MediaGenerationStrategy:
        video = [item.video for item in capabilities if item.video is not None]
        image = [item.image for item in capabilities if item.image is not None]
        first = shot.frame_anchors.first_frame.kind != AnchorKind.NONE
        last = shot.frame_anchors.last_frame.kind != AnchorKind.NONE
        reference_ids = list(dict.fromkeys([
            *shot.reference_artifact_ids,
            *[item.artifact_id for item in (references or [])],
        ]))
        has_references = bool(reference_ids)
        candidates: list[tuple[GenerationStrategyType, str, list[str]]] = []
        if first and last and any(item.first_last_frame for item in video):
            candidates.append((GenerationStrategyType.FIRST_LAST_FRAME_TO_VIDEO,
                               "Both boundary frames are available.", ["first_frame", "last_frame"]))
        if first and any(item.image_to_video or item.first_frame for item in video):
            candidates.append((GenerationStrategyType.FIRST_FRAME_TO_VIDEO,
                               "The opening boundary frame can condition motion.", ["first_frame"]))
        if has_references and any(item.multi_reference for item in video):
            candidates.append((GenerationStrategyType.REFERENCE_TO_VIDEO,
                               "The shot carries selected visual references.", ["multi_reference"]))
        if (has_references and any(item.image_edit for item in image)
                and any(item.image_to_video for item in video)):
            candidates.append((GenerationStrategyType.IMAGE_EDIT_THEN_VIDEO,
                               "References require a provider-neutral image edit before animation.",
                               ["image_edit", "image_to_video"]))
        if any(item.text_to_video for item in video):
            candidates.append((GenerationStrategyType.TEXT_TO_VIDEO,
                               "Text-to-video is the strongest available compatible mode.", []))
        if not candidates:
            raise LookupError("No media provider capability can produce this shot")
        selected = candidates[0]
        fallbacks = [item[0] for item in candidates[1:]]
        too_complex = len(shot.performances) > 3 or (
            shot.duration_seconds > 12 and len(shot.visual_requirements) > 4
        )
        if too_complex:
            return MediaGenerationStrategy(
                strategy_type=GenerationStrategyType.SPLIT_SHOT,
                reason="Shot complexity exceeds the conservative single-generation rule.",
                required_capabilities=[],
                input_artifact_ids=reference_ids,
                fallback_types=[selected[0], *fallbacks],
                split_shot=True,
            )
        return MediaGenerationStrategy(
            strategy_type=selected[0],
            reason=selected[1],
            required_capabilities=selected[2],
            input_artifact_ids=reference_ids,
            fallback_types=fallbacks,
            preparatory_image_edit=selected[0] == GenerationStrategyType.IMAGE_EDIT_THEN_VIDEO,
        )
