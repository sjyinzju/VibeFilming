"""Atomic, typed Studio read model; no execution policy or frontend coordinates."""

from typing import Literal
from pydantic import Field
from movie_agent.domain import (
    ContractModel, WorkflowGraph, GenerationJob, Artifact, EventEnvelope,
    HumanReviewRequest, Evaluation, RepairPlan,
)
from movie_agent.orchestration.runtime.contracts import RoleResult
from movie_agent.media import MediaPreview, MediaRepairPlan, Timeline, VisionInspectionResult
from .views import ProjectSnapshot
from .creative_inputs import CreativeHints
from .review_subjects import ReviewSubject, project_review_subject


class StudioSnapshot(ProjectSnapshot):
    graph: WorkflowGraph
    creative_hints: CreativeHints
    jobs: list[GenerationJob]
    artifacts: list[Artifact]
    reviews: list[HumanReviewRequest]
    roles: list[RoleResult]
    events: list[EventEnvelope]
    evaluations: list[Evaluation] = Field(default_factory=list)
    repairs: list[RepairPlan] = Field(default_factory=list)
    media_mode: Literal["mock", "real", "mixed"] = "mock"
    media_previews: list[MediaPreview] = Field(default_factory=list)
    media_inspections: list[VisionInspectionResult] = Field(default_factory=list)
    media_repairs: list[MediaRepairPlan] = Field(default_factory=list)
    timeline: Timeline | None = None
    media_provider_ids: list[str] = Field(default_factory=list)
    reasoning_provider: str
    review_subjects: list[ReviewSubject] = Field(default_factory=list)
    terminal_revision_scene_id: str | None = None


def studio_snapshot(service, project_id):
    # Synchronous on the single-worker event loop: state and cursor cannot interleave.
    engine = service.engine(project_id)
    artifacts = engine.artifact_store.list_all()
    reviews = engine.human_gates.all()
    events = engine.event_bus.events()
    return StudioSnapshot(**service.snapshot(project_id),
        graph=engine.current_production.graph,
        creative_hints=service.repository.get(project_id).creative_hints,
        jobs=engine._all_jobs(), artifacts=artifacts,
        reviews=reviews, roles=list(engine.role_results.values()),
        review_subjects=[project_review_subject(r, engine.current_production.graph, artifacts, engine.evaluations, events)
                         for r in reviews],
        events=events[-200:],
        evaluations=engine.evaluations, repairs=engine.repair_plans,
        media_previews=[preview for artifact in artifacts
                        if (preview := engine.preview_service.describe(project_id, artifact))],
        media_inspections=engine.media_runtime.inspections,
        media_repairs=engine.media_repair_plans,
        timeline=engine.timeline,
        media_provider_ids=[provider.provider_id for provider in engine.media_runtime.providers.all()],
        reasoning_provider=engine.llm_provider.provider_id,
        terminal_revision_scene_id=service.terminal_revision_scene(project_id))
