"""Frame-anchor planning contracts and a deterministic local planner."""

from __future__ import annotations

from typing import Protocol

from movie_agent.domain import AnchorKind, ContinuityState, FrameAnchor, FrameAnchors, Shot


class FramePlanning(Protocol):
    """Plans boundary-frame dependencies without generating pixels."""

    def plan(
        self,
        shot: Shot,
        previous_shot: Shot | None = None,
        previous_state: ContinuityState | None = None,
    ) -> FrameAnchors: ...


class RuleBasedFramePlanner:
    """Plan anchors from shot adjacency and declared boundary states."""

    def plan(
        self,
        shot: Shot,
        previous_shot: Shot | None = None,
        previous_state: ContinuityState | None = None,
    ) -> FrameAnchors:
        if previous_shot and shot.continuity_chain_id == previous_shot.continuity_chain_id:
            first = FrameAnchor(
                kind=AnchorKind.PREVIOUS_SHOT_LAST_FRAME,
                source_shot_id=previous_shot.shot_id,
                source_artifact_id=(previous_state.last_frame_artifact_id if previous_state else None),
                planned_state=shot.state_before,
                description="Carry the previous shot's last frame into this first frame.",
            )
        elif shot.reference_artifact_ids:
            first = FrameAnchor(
                kind=AnchorKind.EXTERNAL_REFERENCE,
                source_artifact_id=shot.reference_artifact_ids[0],
                planned_state=shot.state_before,
            )
        else:
            first = FrameAnchor(
                kind=AnchorKind.GENERATED,
                planned_state=shot.state_before,
                description="Generate the shot opening boundary from its start state.",
            )

        last = FrameAnchor(
            kind=AnchorKind.GENERATED,
            planned_state=shot.expected_state_after,
            description="Generate the closing boundary from the expected end state.",
        )
        return FrameAnchors(first_frame=first, last_frame=last)

