"""Atomic, typed Studio read model; no execution policy or frontend coordinates."""

from typing import Literal
from pydantic import Field
from movie_agent.domain import (
    ContractModel, WorkflowGraph, GenerationJob, Artifact, EventEnvelope,
    HumanReviewRequest, Evaluation, RepairPlan,
)
from movie_agent.orchestration.runtime.contracts import RoleResult
from movie_agent.media import MediaPreview, MediaReference, MediaRepairPlan, Timeline, VisionInspectionResult
from movie_agent.media import HumanRepairDirective
from movie_agent.media.contracts import CharacterVoiceProfile, DialogueCue
from movie_agent.quality.reports import FilmQualityReport, ShotQualityReport
from movie_agent.quality.references import ReferenceIdentitySet
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
    film_quality: FilmQualityReport | None = None
    quality_history: list[ShotQualityReport] = Field(default_factory=list)
    identity_set: ReferenceIdentitySet | None = None
    voice_profiles: list[CharacterVoiceProfile] = Field(default_factory=list)
    dialogue_cues: list[DialogueCue] = Field(default_factory=list)
    audio_controls: dict = Field(default_factory=dict)
    audio_listening_result: dict | None = None
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
    human_media_directives: list[HumanRepairDirective] = Field(default_factory=list)
    human_overrides: list[dict] = Field(default_factory=list)
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
    from movie_agent.media.download import public_artifact
    artifacts = [public_artifact(a) for a in engine.artifact_store.list_all()]
    reviews = engine.human_gates.all()
    events = engine.event_bus.events()
    providers = [provider.provider_id for provider in engine.media_runtime.providers.all()]
    from movie_agent.services import audio_production as audio
    listening=engine.artifact_store.get('audio_listening_result')
    film_quality=engine.artifact_store.get('film_quality_report')
    identity_set=engine.artifact_store.get('reference_identity_set')
    mock_count = sum(provider.startswith("mock-") for provider in providers)
    return StudioSnapshot(**service.snapshot(project_id),
        film_quality=engine.artifact_store.read_structured(film_quality) if film_quality else None,
        identity_set=engine.artifact_store.read_structured(identity_set) if identity_set else None,
        quality_history=[engine.artifact_store.read_structured(a) for a in engine.artifact_store.list_all()
                         if a.metadata.get('purpose')=='shot_quality_report'],
        voice_profiles=list(audio.profiles(engine).values()),dialogue_cues=audio.cues(engine),
        audio_controls=audio.controls(engine),
        audio_listening_result=engine.artifact_store.read_structured(listening) if listening else None,
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
        human_media_directives=engine.human_media_directives,
        human_overrides=engine.human_overrides,
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
