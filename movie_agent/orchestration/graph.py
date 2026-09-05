"""Mutable façade over the immutable validated WorkflowGraph contract."""

from __future__ import annotations

from movie_agent.domain import (
    EventEnvelope,
    EventType,
    WorkflowEdge,
    WorkflowEdgeType,
    WorkflowGraph,
    WorkflowNode,
    WorkflowNodeStatus,
    utc_now,
)
from movie_agent.execution.events import EventBus


class ProductionGraph:
    """Build and advance an explicit DAG while emitting frontend-ready events."""

    def __init__(
        self,
        graph: WorkflowGraph,
        event_bus: EventBus | None = None,
        trace_id: str | None = None,
    ) -> None:
        self.graph = graph
        self.event_bus = event_bus
        self.trace_id = trace_id or graph.graph_id

    def add_node(self, node: WorkflowNode, *, entry: bool = False) -> WorkflowNode:
        entries = [*self.graph.entry_node_ids, node.node_id] if entry else self.graph.entry_node_ids
        candidate = self.graph.model_copy(
            update={"nodes": [*self.graph.nodes, node], "entry_node_ids": entries}
        )
        self.graph = WorkflowGraph.model_validate(candidate.model_dump())
        self._emit(EventType.NODE_CREATED, node_id=node.node_id, payload={"label": node.label})
        return node

    def add_edge(self, edge: WorkflowEdge) -> WorkflowEdge:
        nodes = [node.model_copy(deep=True) for node in self.graph.nodes]
        for index, node in enumerate(nodes):
            if node.node_id == edge.target_node_id and edge.source_node_id not in node.dependencies:
                nodes[index] = node.model_copy(
                    update={"dependencies": [*node.dependencies, edge.source_node_id]}
                )
        candidate = self.graph.model_copy(update={"nodes": nodes, "edges": [*self.graph.edges, edge]})
        self.graph = WorkflowGraph.model_validate(candidate.model_dump())
        self._emit(
            EventType.EDGE_CREATED,
            payload={
                "edge_id": edge.edge_id,
                "source_node_id": edge.source_node_id,
                "target_node_id": edge.target_node_id,
                "edge_type": edge.edge_type.value,
            },
        )
        return edge

    def node(self, node_id: str) -> WorkflowNode:
        return next(node for node in self.graph.nodes if node.node_id == node_id)

    def ready_nodes(self) -> list[WorkflowNode]:
        statuses = {node.node_id: node.status for node in self.graph.nodes}
        return [
            node
            for node in self.graph.nodes
            if node.status in {WorkflowNodeStatus.PENDING, WorkflowNodeStatus.READY}
            and all(statuses.get(dependency) == WorkflowNodeStatus.SUCCEEDED for dependency in node.dependencies)
        ]

    def set_status(
        self,
        node_id: str,
        status: WorkflowNodeStatus,
        *,
        progress: float | None = None,
    ) -> WorkflowNode:
        previous_status = self.node(node_id).status
        target: WorkflowNode | None = None
        nodes: list[WorkflowNode] = []
        for node in self.graph.nodes:
            if node.node_id != node_id:
                nodes.append(node)
                continue
            changes: dict[str, object] = {"status": status}
            if progress is not None:
                changes["progress"] = progress
            if status == WorkflowNodeStatus.RUNNING:
                changes["started_at"] = node.started_at or utc_now()
            if status in {
                WorkflowNodeStatus.SUCCEEDED,
                WorkflowNodeStatus.FAILED,
                WorkflowNodeStatus.SKIPPED,
                WorkflowNodeStatus.CANCELLED,
            }:
                changes["completed_at"] = utc_now()
            if status == WorkflowNodeStatus.SUCCEEDED:
                changes["progress"] = 1.0
            target = node.model_copy(update=changes)
            nodes.append(target)
        if target is None:
            raise KeyError(node_id)
        self.graph = self.graph.model_copy(update={"nodes": nodes})
        event_type = {
            WorkflowNodeStatus.RUNNING: EventType.NODE_STARTED,
            WorkflowNodeStatus.SUCCEEDED: EventType.NODE_COMPLETED,
            WorkflowNodeStatus.FAILED: EventType.NODE_FAILED,
        }.get(status)
        if status == WorkflowNodeStatus.RUNNING and previous_status == WorkflowNodeStatus.RUNNING:
            event_type = None
        if event_type:
            self._emit(event_type, node_id=node_id, payload={"status": status.value})
        if progress is not None and status == WorkflowNodeStatus.RUNNING:
            self._emit(EventType.NODE_PROGRESS, node_id=node_id, payload={"progress": progress})
        return target

    def activate_incoming_edges(self, node_id: str) -> None:
        for edge in self.graph.edges:
            if edge.target_node_id == node_id:
                self._emit(
                    EventType.EDGE_ACTIVATED,
                    payload={"edge_id": edge.edge_id, "target_node_id": node_id},
                )

    def _emit(
        self,
        event_type: EventType,
        *,
        node_id: str | None = None,
        payload: dict | None = None,
    ) -> None:
        if self.event_bus is None:
            return
        self.event_bus.emit(
            EventEnvelope(
                event_type=event_type,
                project_id=self.graph.project_id,
                trace_id=self.trace_id,
                node_id=node_id,
                payload=payload or {},
            )
        )


PRODUCTION_STAGES: tuple[tuple[str, str, str, str], ...] = (
    ("brief", "brief", "Brief", "Showrunner"),
    ("creative_expansion", "creative", "Creative Expansion", "Creative Producer"),
    ("story_planning", "story", "Story Planning", "Story Architect"),
    ("story_gate", "human_gate", "Story Approval", "Showrunner"),
    ("screenplay", "writing", "Treatment / Screenplay", "Screenwriter"),
    ("bibles", "world", "Story Bible + Visual Bible", "Visual Director"),
    ("scene_planning", "planning", "Scene Planning", "Director"),
    ("shot_planning", "planning", "Shot Planning", "Cinematographer"),
    ("shot_gate", "human_gate", "Shot Plan Approval", "Showrunner"),
    ("asset_planning", "assets", "Asset Planning", "Visual Director"),
    ("storyboard_planning", "storyboard", "Storyboard Planning", "Director"),
    ("shot_production", "production", "Shot Production", "Showrunner"),
    ("technical_qc", "quality", "Technical QC", "Continuity Supervisor"),
    ("visual_semantic_critic", "quality", "Visual/Semantic Critic", "Critic"),
    ("cinematic_critic", "quality", "Cinematic Critic", "Critic"),
    ("repair_accept", "repair", "Repair / Accept", "Repair Planner"),
    ("audio_post", "post", "Audio / Post", "Sound/Post Director"),
    ("rough_cut", "post", "Rough Cut", "Sound/Post Director"),
    ("full_film_review", "quality", "Full Film Review", "Critic"),
    ("final_gate", "human_gate", "Final Cut Approval", "Showrunner"),
    ("final_render", "delivery", "Final Render", "Showrunner"),
)


def build_production_graph(
    project_id: str,
    event_bus: EventBus | None = None,
    trace_id: str | None = None,
) -> ProductionGraph:
    """Create the standard production DAG as data rather than a hard-coded executor."""

    production = ProductionGraph(
        WorkflowGraph(project_id=project_id), event_bus=event_bus, trace_id=trace_id
    )
    previous: WorkflowNode | None = None
    for order, (node_id, node_type, label, role) in enumerate(PRODUCTION_STAGES):
        node = WorkflowNode(
            node_id=node_id,
            node_type=node_type,
            label=label,
            group=node_type,
            role=role,
            display_order=order,
        )
        production.add_node(node, entry=previous is None)
        if previous is not None:
            if node_type == "human_gate":
                edge_type = WorkflowEdgeType.HUMAN_GATE
            elif node_id == "repair_accept":
                edge_type = WorkflowEdgeType.REPAIR
            else:
                edge_type = WorkflowEdgeType.DEPENDENCY
            production.add_edge(
                WorkflowEdge(
                    source_node_id=previous.node_id,
                    target_node_id=node.node_id,
                    edge_type=edge_type,
                )
            )
        previous = node
    return production
