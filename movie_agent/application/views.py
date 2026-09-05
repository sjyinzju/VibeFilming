"""Typed transport projections for OpenAPI and future Studio clients."""

from movie_agent.domain import ContractModel, Project, WorkflowGraph, ProviderCapability
from .repository import ProductionStatus


class ProjectSnapshot(ContractModel):
    """Canonical project snapshot aligned to a durable event cursor."""
    project: Project
    status: ProductionStatus
    failure_code: str | None = None
    event_cursor: str | None = None


class WorkflowSnapshot(ContractModel):
    """Graph snapshot and cursor for lossless SSE handoff."""
    graph: WorkflowGraph
    event_cursor: str | None = None


class CommandAccepted(ContractModel):
    """Acknowledgement of a command, not a claim of production completion."""
    project_id: str
    status: ProductionStatus
    pause_policy: str | None = None


class ProviderView(ContractModel):
    """Public provider capability and availability without runtime credentials."""
    capability: ProviderCapability
    healthy: bool
