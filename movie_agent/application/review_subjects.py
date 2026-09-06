"""Read-only, as-of-review projection over graph edges and immutable artifacts.

Never use the mutable current project/role result as historical review evidence.
Artifact provenance already holds the committed RoleResult, including validation.
"""
from typing import Annotated, Literal

from pydantic import Field, ValidationError
from movie_agent.domain import (
    Artifact, ContractModel, CreativeDirection, Evaluation, EventEnvelope, HumanReviewRequest, Screenplay,
    ShotPlan, StoryBible, WorkflowGraph,
)
from movie_agent.orchestration.runtime.contracts import RoleResult
from movie_agent.orchestration.runtime.registry import RoleRegistry


class StorySubject(ContractModel):
    kind: Literal["StoryBible"] = "StoryBible"
    output: StoryBible


class CreativeSubject(ContractModel):
    kind: Literal["CreativeDirection"] = "CreativeDirection"
    output: CreativeDirection


class ScreenplaySubject(ContractModel):
    kind: Literal["Screenplay"] = "Screenplay"
    output: Screenplay


class ShotSubject(ContractModel):
    kind: Literal["ShotPlan"] = "ShotPlan"
    output: ShotPlan


ReviewContent = Annotated[StorySubject | CreativeSubject | ScreenplaySubject | ShotSubject,
                          Field(discriminator="kind")]


class ReviewSource(ContractModel):
    source_node_id: str
    role_result: RoleResult | None = None
    content: ReviewContent | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    evaluations: list[Evaluation] = Field(default_factory=list)


class ReviewSubject(ContractModel):
    review_id: str
    gate_type: str
    review_node_id: str
    subject_type: str
    subject_title: str
    sources: list[ReviewSource] = Field(default_factory=list)
    unavailable_reason: str | None = None


CONTENT_TYPES = {c.model_fields['kind'].default: c for c in
                 (StorySubject, CreativeSubject, ScreenplaySubject, ShotSubject)}
ROLE_REGISTRY = RoleRegistry()
GATE_TYPES = {
    "story_approval": ("StoryBible", "Story proposal"),
    "screenplay_approval": ("Screenplay", "Screenplay"),
    "shot_plan_approval": ("ShotPlan", "Shot plan"),
    "final_cut_approval": ("FinalCut", "Final cut"),
}


def project_review_subject(review: HumanReviewRequest, graph: WorkflowGraph,
                           artifacts: list[Artifact],
                           evaluations: list[Evaluation] | None = None,
                           events: list[EventEnvelope] | None = None) -> ReviewSubject:
    kind, title = GATE_TYPES.get(review.gate_type, ("Unknown", "Review subject"))
    subject = ReviewSubject(review_id=review.review_id, gate_type=review.gate_type,
        review_node_id=review.node_id, subject_type=kind, subject_title=title)
    # These are explicit graph relations, not naming conventions or display order.
    source_ids = {e.source_node_id for e in graph.edges if e.target_node_id == review.node_id}
    nodes = {n.node_id: n for n in graph.nodes}
    if review.node_id not in nodes:
        source_ids = set()
    # Selection can change after approval; immutable version + creation boundary cannot.
    versions: dict[str, Artifact] = {}
    # Wall clocks can tie on Windows. Durable event order is the authoritative
    # as-of boundary whenever available, including across checkpoint restoration.
    review_index = next((i for i, e in enumerate(events or [])
        if e.event_type == 'human_review_requested'
        and e.payload.get('review_id') == review.review_id), None)
    recorded_versions = None if review_index is None else {
        (e.payload.get('artifact_id'), e.payload.get('version'))
        for e in events[:review_index] if e.event_type == 'artifact_created'}
    for artifact in artifacts:
        if ((artifact.artifact_id, artifact.version) in recorded_versions
                if recorded_versions is not None else artifact.created_at <= review.requested_at):
            prior = versions.get(artifact.artifact_id)
            if prior is None or artifact.version > prior.version:
                versions[artifact.artifact_id] = artifact
    for source_id in sorted(source_ids):
        node = nodes.get(source_id)
        if node is None:
            continue
        refs = set(node.output_refs) | set(review.context_artifact_ids)
        if kind == "FinalCut":
            refs.update(node.input_refs)
            media = [a for a in versions.values() if a.artifact_id in refs
                     and a.artifact_type == "timeline"]
            if media:
                media_ids = {a.artifact_id for a in media}
                subject.sources.append(ReviewSource(source_node_id=source_id, artifacts=media,
                    evaluations=[e for e in evaluations or []
                                 if e.target_artifact_id in media_ids
                                 and e.created_at <= review.requested_at]))
            continue
        for artifact in versions.values():
            if artifact.artifact_id not in refs:
                continue
            try:
                result = RoleResult.model_validate(artifact.provenance.parameters.get('role_result'))
                committed_kind = ROLE_REGISTRY.committed_target(
                    ROLE_REGISTRY.get(result.invocation.role_id)).__name__
                if (not result.committed or result.invocation.node_id != source_id
                        or result.invocation.project_id != review.project_id
                        or committed_kind != kind):
                    continue
                content = CONTENT_TYPES[kind](output=result.output)
            except (ValidationError, KeyError):
                continue
            subject.sources.append(ReviewSource(source_node_id=source_id,
                role_result=result, content=content, artifacts=[artifact]))
    if not subject.sources:
        subject.unavailable_reason = "Review subject unavailable. No committed evidence is linked to this review."
    return subject
