"""Persistence boundary for project command metadata."""

from typing import Protocol
from enum import StrEnum
from movie_agent.domain import ContractModel, Project


class ProductionStatus(StrEnum):
    """Application status, distinct from node and job state."""
    CREATED = "created"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    WAITING_HUMAN = "waiting_human"
    COMPLETED = "completed"
    FAILED = "failed"


class ProjectRecord(ContractModel):
    """Durable command metadata and initial project; execution snapshots stay in checkpoints."""
    project: Project
    status: ProductionStatus = ProductionStatus.CREATED
    failure_code: str | None = None


class ProjectRepository(Protocol):
    """Replaceable application repository, implemented locally in the composition root."""
    def save(self, record: ProjectRecord) -> None: ...
    def get(self, project_id: str) -> ProjectRecord: ...
    def list_ids(self) -> list[str]: ...
