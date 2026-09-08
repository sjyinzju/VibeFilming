"""Atomic, typed Studio read model; no execution policy or frontend coordinates."""

from typing import Literal
from pydantic import Field
from movie_agent.domain import (
    ContractModel, WorkflowGraph, GenerationJob, Artifact, EventEnvelope,
    HumanReviewRequest, Evaluation, RepairPlan,
)
from movie_agent.orchestration.runtime.contracts import RoleResult
from movie_agent.media import MediaPreview, MediaReference, MediaRepairPlan, Timeline, VisionInspectionResult
from .views import ProjectSnapshot
from .creative_inputs import CreativeHints
from .review_subjects import ReviewSubject, project_review_subject
from movie_agent.providers.media import ImageProvider, VideoProvider, VisionProvider, AudioProvider, PostProcessor
from movie_agent.media.execution_graph import ProviderExecutionGraphView
from .provider_execution import project_provider_execution_graphs
from movie_agent.model_services.contracts import ModelServiceDescriptor
from movie_agent.model_services.resources import (
    ResourceSnapshot, ResourceLease, SchedulerDecision, ResourceObservation,
)


class ResourceRuntimeView(ContractModel):
    enabled: bool = False
    snapshot: ResourceSnapshot | None = None
    services: list[ModelServiceDescriptor] = Field(default_factory=list)
    leases: list[ResourceLease] = Field(default_factory=list)
    decisions: list[SchedulerDecision] = Field(default_factory=list)
    observations: list[ResourceObservation] = Field(default_factory=list)
    oom_headroom_bytes: dict[str, int] = Field(default_factory=dict)


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
    media_provider_bindings: dict[str, str] = Field(default_factory=dict)
    media_references: list[MediaReference] = Field(default_factory=list)
    reasoning_provider: str
    review_subjects: list[ReviewSubject] = Field(default_factory=list)
    terminal_revision_scene_id: str | None = None
    provider_execution_graphs: list[ProviderExecutionGraphView] = Field(default_factory=list)
    resource_runtime: ResourceRuntimeView | None = None


def studio_snapshot(service, project_id):
    # Synchronous on the single-worker event loop: state and cursor cannot interleave.
    engine = service.engine(project_id)
    artifacts = engine.artifact_store.list_all()
    reviews = engine.human_gates.all()
    events = engine.event_bus.events()
    providers = [provider.provider_id for provider in engine.media_runtime.providers.all()]
    mock_count = sum(provider.startswith("mock-") for provider in providers)
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
        media_provider_ids=providers,
        media_provider_bindings={name: provider.provider_id
            for name, interface in (("image", ImageProvider), ("video", VideoProvider),
                ("vision", VisionProvider), ("audio", AudioProvider), ("post", PostProcessor))
            for provider in engine.media_runtime.providers.all() if isinstance(provider, interface)},
        media_mode="mock" if mock_count == len(providers) else "mixed" if mock_count else "real",
        media_references=engine.reference_bank.all(),
        reasoning_provider=engine.llm_provider.provider_id,
        resource_runtime=(engine.media_runtime.runtime_coordinator.view()
                          if engine.media_runtime.runtime_coordinator else {}),
        terminal_revision_scene_id=service.terminal_revision_scene(project_id),
        provider_execution_graphs=project_provider_execution_graphs(artifacts, events))
