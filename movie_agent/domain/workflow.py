"""Explicit production DAG, event stream, and human review contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from movie_agent.domain.base import ContractModel, JSONValue, new_id, utc_now
from movie_agent.domain.enums import (
    EventType,
    HumanGateType,
    ReviewStatus,
    WorkflowEdgeType,
    WorkflowNodeStatus,
)


class WorkflowNode(ContractModel):
    """Executable production node plus frontend-neutral display metadata."""

    node_id: str = Field(default_factory=lambda: new_id("node"))
    node_type: str
    label: str
    group: str
    parent_id: str | None = None
    status: WorkflowNodeStatus = WorkflowNodeStatus.PENDING
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    role: str
    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    children: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    display_order: int | None = None
    color_role: str | None = None
    icon_hint: str | None = None


class WorkflowEdge(ContractModel):
    """Directed dependency or conditional route in a production graph."""

    edge_id: str = Field(default_factory=lambda: new_id("edge"))
    source_node_id: str
    target_node_id: str
    edge_type: WorkflowEdgeType = WorkflowEdgeType.DEPENDENCY
    condition: str | None = None
    label: str | None = None


class WorkflowGraph(ContractModel):
    """Validated acyclic production graph independent of orchestration engines."""

    graph_id: str = Field(default_factory=lambda: new_id("graph"))
    project_id: str
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)
    entry_node_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph(self) -> "WorkflowGraph":
        """Reject duplicate IDs, dangling edges, and cycles."""

        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("workflow node IDs must be unique")
        known = set(node_ids)
        edge_ids = [edge.edge_id for edge in self.edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("workflow edge IDs must be unique")
        for edge in self.edges:
            if edge.source_node_id not in known or edge.target_node_id not in known:
                raise ValueError("workflow edge references an unknown node")
        if any(entry not in known for entry in self.entry_node_ids):
            raise ValueError("entry node references an unknown node")

        adjacency: dict[str, list[str]] = {node_id: [] for node_id in known}
        indegree = {node_id: 0 for node_id in known}
        for edge in self.edges:
            adjacency[edge.source_node_id].append(edge.target_node_id)
            indegree[edge.target_node_id] += 1
        ready = [node_id for node_id, degree in indegree.items() if degree == 0]
        visited = 0
        while ready:
            current = ready.pop()
            visited += 1
            for target in adjacency[current]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        if visited != len(known):
            raise ValueError("workflow graph must be acyclic")
        return self


class WorkflowEvent(ContractModel):
    """Typed event body carried by an EventEnvelope."""

    event_type: EventType
    payload: dict[str, JSONValue] = Field(default_factory=dict)


class EventEnvelope(ContractModel):
    """Uniform event envelope consumed by future workflow canvases."""

    event_id: str = Field(default_factory=lambda: new_id("event"))
    event_type: EventType
    timestamp: datetime = Field(default_factory=utc_now)
    project_id: str
    trace_id: str
    node_id: str | None = None
    job_id: str | None = None
    payload: dict[str, JSONValue] = Field(default_factory=dict)


class HumanReviewRequest(ContractModel):
    """Persisted pause point that can be resolved and resumed later."""

    review_id: str = Field(default_factory=lambda: new_id("review"))
    project_id: str
    node_id: str
    gate_type: HumanGateType
    status: ReviewStatus = ReviewStatus.PENDING
    question: str
    context_artifact_ids: list[str] = Field(default_factory=list)
    requested_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None
    resolution_notes: str | None = None
    inspection_result_id: str | None = None
    target_artifact_id: str | None = None
    target_artifact_version: int | None = Field(default=None, ge=1)
    target_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    directive_id: str | None = None
    superseded_at: datetime | None = None
