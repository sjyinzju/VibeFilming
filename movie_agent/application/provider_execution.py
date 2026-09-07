"""Read-model projection for durable provider execution graphs."""

from __future__ import annotations

from movie_agent.domain import Artifact, EventEnvelope
from movie_agent.media.execution_graph import (
    ProviderExecutionGraphUpdate,
    ProviderExecutionGraphView,
    apply_provider_execution_update,
)


def project_provider_execution_graphs(
    artifacts: list[Artifact], events: list[EventEnvelope]
) -> list[ProviderExecutionGraphView]:
    """Rebuild current graphs from final Artifact provenance and live durable events."""

    graphs: dict[str, ProviderExecutionGraphView] = {}
    for event in events:
        payload = event.payload.get("provider_execution_graph")
        if isinstance(payload, dict):
            try:
                graph = ProviderExecutionGraphView.model_validate(payload)
            except ValueError:
                graph = None
            if graph is not None:
                graphs[graph.execution_id] = graph
        update_payload = event.payload.get("provider_execution_update")
        if isinstance(update_payload, dict):
            try:
                update = ProviderExecutionGraphUpdate.model_validate(update_payload)
            except ValueError:
                continue
            graph = graphs.get(update.execution_id)
            if graph is not None:
                graphs[update.execution_id] = apply_provider_execution_update(graph, update)
    # A committed output contains the provider's final graph, including output
    # Artifact associations that were not yet available in live progress events.
    for artifact in artifacts:
        payload = artifact.provenance.parameters.get("provider_execution_graph")
        if isinstance(payload, dict):
            try:
                graph = ProviderExecutionGraphView.model_validate(payload)
            except ValueError:
                continue
            graphs[graph.execution_id] = graph
    return sorted(graphs.values(), key=lambda item: (item.created_at, item.execution_id))
