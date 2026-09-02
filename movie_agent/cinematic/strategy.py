"""Capability-driven generation strategy planner."""

from __future__ import annotations

from movie_agent.domain import (
    AnchorKind,
    ContinuityState,
    GenerationStrategy,
    GenerationStrategyType,
    ProviderCapability,
    Shot,
)


class GenerationStrategyPlanner:
    """Select a provider-neutral strategy from anchors, references, and capabilities."""

    def plan(
        self,
        shot: Shot,
        available_capabilities: list[ProviderCapability],
        *,
        continuity: ContinuityState | None = None,
    ) -> GenerationStrategy:
        """Plan from the shot, its continuity boundary, and available capabilities."""

        # The explicit parameter keeps the planner boundary ready for policies that
        # inspect state. Anchor planning currently carries the state-derived choice.
        _ = continuity
        supported = {
            strategy
            for capability in available_capabilities
            for strategy in capability.generation_strategies
        }
        first = shot.frame_anchors.first_frame.kind != AnchorKind.NONE
        last = shot.frame_anchors.last_frame.kind != AnchorKind.NONE

        candidates: list[tuple[GenerationStrategyType, str, list[str]]] = []
        if first and last:
            candidates.append(
                (
                    GenerationStrategyType.FIRST_LAST_FRAME_TO_VIDEO,
                    "Both boundary frames are planned, maximizing temporal control.",
                    ["first_frame", "last_frame"],
                )
            )
        if first:
            candidates.append(
                (
                    GenerationStrategyType.FIRST_FRAME_TO_VIDEO,
                    "An opening boundary frame is available.",
                    ["first_frame"],
                )
            )
        if shot.reference_artifact_ids:
            candidates.append(
                (
                    GenerationStrategyType.REFERENCE_TO_VIDEO,
                    "Shot declares visual reference artifacts.",
                    ["reference_images"],
                )
            )
        candidates.extend(
            [
                (
                    GenerationStrategyType.TEXT_TO_VIDEO,
                    "No stronger supported conditioning strategy is required.",
                    [],
                ),
                (
                    GenerationStrategyType.STATIC_PLUS_POST_CAMERA,
                    "Fallback for providers without direct video capability.",
                    ["image_generation", "post_camera"],
                ),
            ]
        )

        for strategy_type, reason, required in candidates:
            if strategy_type in supported:
                fallbacks = [
                    candidate_type
                    for candidate_type, _, _ in candidates
                    if candidate_type != strategy_type and candidate_type in supported
                ]
                return GenerationStrategy(
                    strategy_type=strategy_type,
                    reason=reason,
                    required_capabilities=required,
                    input_artifact_ids=list(shot.reference_artifact_ids),
                    fallback_types=fallbacks,
                )
        raise LookupError("No available provider capability can produce this shot")
