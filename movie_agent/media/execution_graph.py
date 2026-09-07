"""Provider-neutral, read-only execution graph observability contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from movie_agent.domain.base import ContractModel, JSONValue, utc_now


class ProviderExecutionNodeState(StrEnum):
    WAITING = "waiting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class ProviderExecutionGraphNode(ContractModel):
    remote_node_id: str = Field(min_length=1)
    class_type: str = Field(min_length=1)
    display_label: str = Field(min_length=1)
    category: str = "other"
    runtime_state: ProviderExecutionNodeState = ProviderExecutionNodeState.WAITING
    progress: float | None = Field(default=None, ge=0, le=1)
    progress_is_determinate: bool = False
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    input_summary: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)


class ProviderExecutionGraphEdge(ContractModel):
    edge_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    source_slot: int | None = Field(default=None, ge=0)
    target_slot: str | None = None


class ProviderExecutionGraphView(ContractModel):
    execution_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    workflow_template_id: str = Field(min_length=1)
    workflow_template_version: str = Field(min_length=1)
    workflow_template_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding_manifest_id: str | None = None
    binding_manifest_version: str | None = None
    parent_node_id: str | None = None
    parent_job_id: str | None = None
    project_id: str | None = None
    scene_id: str | None = None
    shot_id: str | None = None
    remote_prompt_id: str | None = None
    nodes: list[ProviderExecutionGraphNode]
    edges: list[ProviderExecutionGraphEdge]
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class ProviderExecutionGraphUpdate(ContractModel):
    execution_id: str = Field(min_length=1)
    remote_event: str = Field(min_length=1)
    remote_node_id: str | None = None
    runtime_state: ProviderExecutionNodeState | None = None
    progress: float | None = Field(default=None, ge=0, le=1)
    progress_is_determinate: bool = False
    activity: str | None = None
    error: str | None = None
    timestamp: datetime = Field(default_factory=utc_now)


def build_provider_execution_graph(
    *,
    execution_id: str,
    provider: str,
    workflow_template_id: str,
    workflow_template_version: str,
    workflow_template_hash: str,
    binding_manifest_id: str,
    binding_manifest_version: str,
    prompt: dict[str, dict[str, JSONValue]],
    node_metadata: dict[str, dict[str, JSONValue]],
    parent_node_id: str | None,
    parent_job_id: str,
    project_id: str,
    scene_id: str | None,
    shot_id: str | None,
) -> ProviderExecutionGraphView:
    """Project an executable provider graph without GUI coordinates or editable values."""

    nodes = []
    edges = []
    for node_id, node in prompt.items():
        class_type = str(node["class_type"])
        metadata = node_metadata.get(node_id, {})
        nodes.append(ProviderExecutionGraphNode(
            remote_node_id=node_id,
            class_type=class_type,
            display_label=str(metadata.get("display_label") or node.get("_meta", {}).get("title") or class_type),
            category=str(metadata.get("category") or "other"),
            input_summary=sorted(str(name) for name in node.get("inputs", {})),
        ))
        for input_name, value in node.get("inputs", {}).items():
            if (
                isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], str)
                and value[0] in prompt
                and isinstance(value[1], int)
            ):
                edges.append(ProviderExecutionGraphEdge(
                    edge_id=f"{value[0]}:{value[1]}->{node_id}:{input_name}",
                    source=value[0], target=node_id,
                    source_slot=value[1], target_slot=input_name,
                ))
    return ProviderExecutionGraphView(
        execution_id=execution_id,
        provider=provider,
        workflow_template_id=workflow_template_id,
        workflow_template_version=workflow_template_version,
        workflow_template_hash=workflow_template_hash,
        binding_manifest_id=binding_manifest_id,
        binding_manifest_version=binding_manifest_version,
        parent_node_id=parent_node_id,
        parent_job_id=parent_job_id,
        project_id=project_id,
        scene_id=scene_id,
        shot_id=shot_id,
        nodes=nodes,
        edges=edges,
    )


def apply_provider_execution_update(
    graph: ProviderExecutionGraphView,
    update: ProviderExecutionGraphUpdate,
) -> ProviderExecutionGraphView:
    if graph.execution_id != update.execution_id:
        return graph
    terminal = update.remote_event in {
        "execution_success", "execution_error", "execution_interrupted"
    }
    nodes: list[ProviderExecutionGraphNode] = []
    for node in graph.nodes:
        changes: dict[str, object] = {}
        if update.remote_event == "execution_success":
            changes.update({
                "runtime_state": ProviderExecutionNodeState.SUCCEEDED,
                "progress": 1.0 if node.progress_is_determinate else node.progress,
                "completed_at": node.completed_at or update.timestamp,
            })
        elif node.remote_node_id == update.remote_node_id and update.runtime_state is not None:
            changes.update({
                "runtime_state": update.runtime_state,
                "progress": update.progress,
                "progress_is_determinate": update.progress_is_determinate,
                "error": update.error,
            })
            if update.runtime_state == ProviderExecutionNodeState.RUNNING:
                changes["started_at"] = node.started_at or update.timestamp
            elif update.runtime_state in {
                ProviderExecutionNodeState.SUCCEEDED,
                ProviderExecutionNodeState.FAILED,
                ProviderExecutionNodeState.INTERRUPTED,
            }:
                changes["completed_at"] = node.completed_at or update.timestamp
        elif (
            update.remote_event == "execution_interrupted"
            and update.remote_node_id is None
            and node.runtime_state in {
                ProviderExecutionNodeState.WAITING,
                ProviderExecutionNodeState.RUNNING,
            }
        ):
            changes.update({
                "runtime_state": ProviderExecutionNodeState.INTERRUPTED,
                "completed_at": node.completed_at or update.timestamp,
            })
        elif (
            update.remote_event == "executing"
            and node.runtime_state == ProviderExecutionNodeState.RUNNING
        ):
            changes.update({
                "runtime_state": ProviderExecutionNodeState.SUCCEEDED,
                "completed_at": node.completed_at or update.timestamp,
            })
        nodes.append(node.model_copy(update=changes) if changes else node)
    return graph.model_copy(update={
        "nodes": nodes,
        "completed_at": update.timestamp if terminal else graph.completed_at,
    })
