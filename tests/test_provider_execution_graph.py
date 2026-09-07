"""Provider execution graphs remain observable, durable, and outside Domain contracts."""

from __future__ import annotations

from movie_agent.application.provider_execution import project_provider_execution_graphs
from movie_agent.domain import Artifact, ArtifactType, EventEnvelope, EventType, Provenance
from movie_agent.domain.base import utc_now
from movie_agent.media import VideoGenerationRequest
from movie_agent.media.execution_graph import (
    ProviderExecutionGraphUpdate,
    ProviderExecutionNodeState,
    apply_provider_execution_update,
    build_provider_execution_graph,
)


def graph_fixture():
    prompt = {
        "load": {"class_type": "LoadImage", "inputs": {"image": "first.png"}},
        "sample": {"class_type": "Sampler", "inputs": {"image": ["load", 0]}},
        "save": {"class_type": "SaveVideo", "inputs": {"video": ["sample", 1]}},
    }
    return build_provider_execution_graph(
        execution_id="prompt-1", provider="comfyui-video",
        workflow_template_id="workflow", workflow_template_version="1.0.0",
        workflow_template_hash="a" * 64,
        binding_manifest_id="bindings", binding_manifest_version="1.0.0",
        prompt=prompt,
        node_metadata={"sample": {"display_label": "Sampling", "category": "sampling"}},
        parent_node_id="shot_production", parent_job_id="job-1",
        project_id="project-1", scene_id="scene-1", shot_id="shot-1",
    )


def test_graph_derives_real_nodes_edges_and_runtime_progress_without_domain_pollution() -> None:
    graph = graph_fixture()
    assert [node.remote_node_id for node in graph.nodes] == ["load", "sample", "save"]
    assert {(edge.source, edge.target, edge.source_slot, edge.target_slot) for edge in graph.edges} == {
        ("load", "sample", 0, "image"), ("sample", "save", 1, "video")
    }
    assert graph.nodes[1].display_label == "Sampling"
    assert "provider_execution_graph" not in VideoGenerationRequest.model_fields

    started = apply_provider_execution_update(graph, ProviderExecutionGraphUpdate(
        execution_id="prompt-1", remote_event="progress_state", remote_node_id="sample",
        runtime_state=ProviderExecutionNodeState.RUNNING,
        progress=0.43, progress_is_determinate=True,
    ))
    sampling = started.nodes[1]
    assert sampling.runtime_state == ProviderExecutionNodeState.RUNNING
    assert sampling.progress == 0.43 and sampling.progress_is_determinate
    completed = apply_provider_execution_update(started, ProviderExecutionGraphUpdate(
        execution_id="prompt-1", remote_event="execution_success", timestamp=utc_now()
    ))
    assert all(node.runtime_state == ProviderExecutionNodeState.SUCCEEDED for node in completed.nodes)
    assert completed.completed_at is not None


def test_graph_projects_from_durable_media_events_and_survives_completion() -> None:
    graph = graph_fixture()
    start = EventEnvelope(
        event_type=EventType.MEDIA_JOB_PROGRESS, project_id="project-1", trace_id="trace-1",
        node_id="shot_production", job_id="job-1",
        payload={"provider_execution_graph": graph.model_dump(mode="json")},
    )
    update = ProviderExecutionGraphUpdate(
        execution_id="prompt-1", remote_event="executing", remote_node_id="sample",
        runtime_state=ProviderExecutionNodeState.RUNNING,
    )
    progress = EventEnvelope(
        event_type=EventType.MEDIA_JOB_PROGRESS, project_id="project-1", trace_id="trace-1",
        node_id="shot_production", job_id="job-1",
        payload={"provider_execution_update": update.model_dump(mode="json")},
    )
    projected = project_provider_execution_graphs([], [start, progress])
    assert len(projected) == 1
    assert projected[0].nodes[1].runtime_state == ProviderExecutionNodeState.RUNNING


def test_graph_preserves_failure_and_interruption_as_real_terminal_states() -> None:
    failed = apply_provider_execution_update(graph_fixture(), ProviderExecutionGraphUpdate(
        execution_id="prompt-1", remote_event="execution_error", remote_node_id="sample",
        runtime_state=ProviderExecutionNodeState.FAILED, error="fixture failure",
    ))
    assert failed.nodes[1].runtime_state == ProviderExecutionNodeState.FAILED
    assert failed.nodes[1].error == "fixture failure"
    assert failed.completed_at is not None

    interrupted = apply_provider_execution_update(graph_fixture(), ProviderExecutionGraphUpdate(
        execution_id="prompt-1", remote_event="execution_interrupted",
        runtime_state=ProviderExecutionNodeState.INTERRUPTED,
    ))
    assert all(
        node.runtime_state == ProviderExecutionNodeState.INTERRUPTED
        for node in interrupted.nodes
    )
    assert interrupted.completed_at is not None


def test_committed_artifact_graph_keeps_final_output_associations() -> None:
    graph = graph_fixture()
    live = EventEnvelope(
        event_type=EventType.MEDIA_JOB_PROGRESS, project_id="project-1", trace_id="trace-1",
        payload={"provider_execution_graph": graph.model_dump(mode="json")},
    )
    final = graph.model_copy(update={
        "nodes": [
            node.model_copy(update={"output_artifact_ids": ["video-1"]})
            if node.remote_node_id == "save" else node
            for node in graph.nodes
        ],
    })
    artifact = Artifact(
        artifact_id="video-1", artifact_type=ArtifactType.VIDEO,
        source_job_id="job-1", uri="artifact://video-1/v1",
        provenance=Provenance(parameters={
            "provider_execution_graph": final.model_dump(mode="json"),
        }),
    )
    projected = project_provider_execution_graphs([artifact], [live])
    assert projected[0].nodes[2].output_artifact_ids == ["video-1"]
