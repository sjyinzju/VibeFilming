"""Human review pause/resume service independent of any UI."""

from __future__ import annotations

from movie_agent.domain import (
    EventEnvelope,
    EventType,
    HumanGateType,
    HumanReviewRequest,
    ReviewStatus,
    utc_now,
)
from movie_agent.execution.events import EventBus


class HumanGateManager:
    """Create and resolve persisted human review requests."""

    def __init__(self, event_bus: EventBus, trace_id: str) -> None:
        self.event_bus = event_bus
        self.trace_id = trace_id
        self._reviews: dict[str, HumanReviewRequest] = {}

    def request(
        self,
        project_id: str,
        node_id: str,
        gate_type: HumanGateType,
        question: str,
        context_artifact_ids: list[str] | None = None,
        *, inspection=None, target_artifact=None,
    ) -> HumanReviewRequest:
        review = HumanReviewRequest(
            project_id=project_id,
            node_id=node_id,
            gate_type=gate_type,
            question=question,
            context_artifact_ids=context_artifact_ids or [],
            inspection_result_id=inspection.result_id if inspection else None,
            target_artifact_id=inspection.target_artifact_id if inspection else target_artifact.artifact_id if target_artifact else None,
            target_artifact_version=inspection.target_artifact_version if inspection else target_artifact.version if target_artifact else None,
            target_sha256=inspection.target_sha256 if inspection else target_artifact.metadata['sha256'] if target_artifact else None,
        )
        self._reviews[review.review_id] = review
        self._emit(review, EventType.HUMAN_REVIEW_REQUESTED)
        return review

    def resolve(
        self,
        review_id: str,
        approved: bool,
        notes: str | None = None,
    ) -> HumanReviewRequest:
        review = self._reviews[review_id]
        if review.status != ReviewStatus.PENDING:
            raise ValueError("human review is already resolved")
        updated = review.model_copy(
            update={
                "status": ReviewStatus.APPROVED if approved else ReviewStatus.REJECTED,
                "resolved_at": utc_now(),
                "resolution_notes": notes,
            }
        )
        self._reviews[review_id] = updated
        self._emit(updated, EventType.HUMAN_REVIEW_RESOLVED)
        return updated

    def get(self, review_id: str) -> HumanReviewRequest:
        return self._reviews[review_id]

    def all(self) -> list[HumanReviewRequest]:
        """Return all review requests in creation order."""

        return list(self._reviews.values())

    def pending_for_node(self, node_id: str) -> HumanReviewRequest | None:
        """Find a persisted unresolved gate so resume does not create a duplicate."""

        return next(
            (
                review
                for review in self._reviews.values()
                if review.node_id == node_id and review.status == ReviewStatus.PENDING and review.superseded_at is None
            ),
            None,
        )

    def restore(self, review: HumanReviewRequest) -> HumanReviewRequest:
        """Restore a persisted request without re-emitting its historical event."""

        existing = self._reviews.get(review.review_id)
        if existing is not None and existing != review:
            raise ValueError(f"conflicting review restoration {review.review_id}")
        self._reviews[review.review_id] = review.model_copy(deep=True)
        return self._reviews[review.review_id]

    def _emit(self, review: HumanReviewRequest, event_type: EventType) -> None:
        self.event_bus.emit(
            EventEnvelope(
                event_type=event_type,
                project_id=review.project_id,
                trace_id=self.trace_id,
                node_id=review.node_id,
                payload={
                    "review_id": review.review_id,
                    "gate_type": review.gate_type.value,
                    "status": review.status.value,
                },
            )
        )
