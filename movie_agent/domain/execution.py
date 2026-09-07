"""Artifacts, immutable versions, jobs, and checkpoint snapshot contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from movie_agent.domain.base import ContractModel, JSONValue, Provenance, new_id, utc_now
from movie_agent.domain.enums import ArtifactType, JobStatus, ResourceClass
from movie_agent.domain.quality import Evaluation, RepairPlan


class ArtifactVersion(ContractModel):
    """Immutable version record for an artifact identity."""

    version: int = Field(ge=1)
    uri: str = Field(min_length=1)
    source_job_id: str | None = None
    parent_artifact_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, JSONValue] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=Provenance)
    created_at: datetime = Field(default_factory=utc_now)


class Artifact(ContractModel):
    """One immutable material version addressed by stable artifact ID and version."""

    artifact_id: str = Field(default_factory=lambda: new_id("artifact"))
    artifact_type: ArtifactType
    uri: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    source_job_id: str | None = None
    parent_artifact_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, JSONValue] = Field(default_factory=dict)
    selected: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    provenance: Provenance = Field(default_factory=Provenance)

    def as_version(self) -> ArtifactVersion:
        """Return the immutable value record used by registries and manifests."""

        return ArtifactVersion(
            version=self.version,
            uri=self.uri,
            source_job_id=self.source_job_id,
            parent_artifact_ids=self.parent_artifact_ids,
            metadata=self.metadata,
            provenance=self.provenance,
            created_at=self.created_at,
        )


class GenerationJob(ContractModel):
    """Durable lifecycle record for any generation or production task."""

    job_id: str = Field(default_factory=lambda: new_id("job"))
    project_id: str
    node_id: str | None = None
    scene_id: str | None = None
    shot_id: str | None = None
    continuity_chain_id: str | None = None
    task: str
    provider_id: str | None = None
    strategy_type: str | None = None
    status: JobStatus = JobStatus.PENDING
    dependencies: list[str] = Field(default_factory=list)
    priority: int = 0
    resource_class: ResourceClass = ResourceClass.MEDIUM
    retry_budget: int = Field(default=2, ge=0)
    retry_count: int = Field(default=0, ge=0)
    idempotency_key: str = Field(min_length=1)
    cancellation_requested: bool = False
    remote_cancellation_dispatched: bool = False
    remote_status: JobStatus | None = None
    created_at: datetime = Field(default_factory=utc_now)
    queued_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    progress_is_determinate: bool = False
    activity: str | None = None
    failure_reason: str | None = None
    input_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    related_artifact_ids: list[str] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)

    @model_validator(mode="after")
    def validate_retry_count(self) -> "GenerationJob":
        """Prevent a persisted job from silently exceeding its budget."""

        if self.retry_count > self.retry_budget:
            raise ValueError("retry_count cannot exceed retry_budget")
        return self


class CheckpointSnapshot(ContractModel):
    """Serializable recovery point for a long-running production."""

    checkpoint_id: str = Field(default_factory=lambda: new_id("checkpoint"))
    project_id: str
    workflow_graph: dict[str, JSONValue]
    project_state: dict[str, JSONValue]
    completed_node_ids: list[str] = Field(default_factory=list)
    active_jobs: list[GenerationJob] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    evaluations: list[Evaluation] = Field(default_factory=list)
    repair_plans: list[RepairPlan] = Field(default_factory=list)
    retry_state: dict[str, int] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
