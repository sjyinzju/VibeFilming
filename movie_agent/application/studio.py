"""Atomic, typed Studio read model; no execution policy or frontend coordinates."""

from typing import Literal
from pydantic import Field
from movie_agent.domain import (
    ContractModel, WorkflowGraph, GenerationJob, Artifact, EventEnvelope,
    HumanReviewRequest, Evaluation, RepairPlan,
)
from movie_agent.orchestration.runtime.contracts import RoleResult
from .views import ProjectSnapshot
from .creative_inputs import CreativeHints


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
    media_mode: Literal["mock"] = "mock"
    reasoning_provider: str


def studio_snapshot(service, project_id):
    # Synchronous on the single-worker event loop: state and cursor cannot interleave.
    engine = service.engine(project_id)
    return StudioSnapshot(**service.snapshot(project_id),
        graph=engine.current_production.graph,
        creative_hints=service.repository.get(project_id).creative_hints,
        jobs=engine._all_jobs(), artifacts=engine.artifact_store.list_all(),
        reviews=engine.human_gates.all(), roles=list(engine.role_results.values()),
        events=engine.event_bus.events()[-200:],
        evaluations=engine.evaluations, repairs=engine.repair_plans,
        reasoning_provider=engine.llm_provider.provider_id)
