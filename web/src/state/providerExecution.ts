import type { Event, ProviderExecutionGraph } from '../api/types';

type GraphUpdate = {
  execution_id: string;
  remote_event: string;
  remote_node_id?: string | null;
  runtime_state?: ProviderExecutionGraph['nodes'][number]['runtime_state'] | null;
  progress?: number | null;
  progress_is_determinate?: boolean;
  activity?: string | null;
  error?: string | null;
  timestamp: string;
};

export function applyProviderExecutionEvent(
  graphs: ProviderExecutionGraph[],
  event: Event,
): ProviderExecutionGraph[] {
  const payload = event.payload as Record<string, unknown>;
  const full = payload.provider_execution_graph as ProviderExecutionGraph | null | undefined;
  let next = graphs;
  if (full?.execution_id) {
    next = [...graphs.filter((graph) => graph.execution_id !== full.execution_id), full];
  }
  const update = payload.provider_execution_update as GraphUpdate | null | undefined;
  if (!update?.execution_id) return next;
  return next.map((graph) => {
    if (graph.execution_id !== update.execution_id) return graph;
    const terminal = ['execution_success', 'execution_error', 'execution_interrupted'].includes(
      update.remote_event,
    );
    return {
      ...graph,
      completed_at: terminal ? update.timestamp : graph.completed_at,
      nodes: graph.nodes.map((node) => {
        if (update.remote_event === 'execution_success') {
          return {
            ...node,
            runtime_state: 'succeeded' as const,
            completed_at: node.completed_at || update.timestamp,
          };
        }
        if (node.remote_node_id === update.remote_node_id && update.runtime_state) {
          return {
            ...node,
            runtime_state: update.runtime_state,
            progress: update.progress ?? null,
            progress_is_determinate: update.progress_is_determinate ?? false,
            error: update.error ?? null,
            started_at:
              update.runtime_state === 'running'
                ? node.started_at || update.timestamp
                : node.started_at,
            completed_at: ['succeeded', 'failed', 'interrupted'].includes(update.runtime_state)
              ? node.completed_at || update.timestamp
              : node.completed_at,
          };
        }
        if (
          update.remote_event === 'execution_interrupted' &&
          !update.remote_node_id &&
          ['waiting', 'running'].includes(node.runtime_state)
        ) {
          return {
            ...node,
            runtime_state: 'interrupted' as const,
            completed_at: node.completed_at || update.timestamp,
          };
        }
        if (update.remote_event === 'executing' && node.runtime_state === 'running') {
          return {
            ...node,
            runtime_state: 'succeeded' as const,
            completed_at: node.completed_at || update.timestamp,
          };
        }
        return node;
      }),
    };
  });
}
