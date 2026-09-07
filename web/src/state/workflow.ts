import type { Event, Snapshot, WorkflowNode } from '../api/types';
import { applyProviderExecutionEvent } from './providerExecution';

export function mergeEvents(a: Event[], b: Event[]): Event[] {
  return [...new Map([...a, ...b].map((event) => [event.event_id, event])).values()]
    .sort((x, y) => x.timestamp.localeCompare(y.timestamp))
    .slice(-200);
}

export function reconcileSnapshot(snapshot: Snapshot, history: Event[], buffered: Event[]) {
  const covered = new Set(snapshot.events.map((e) => e.event_id));
  const boundary = buffered.findIndex((e) => e.event_id === snapshot.event_cursor);
  const newer = (boundary >= 0 ? buffered.slice(boundary + 1) : buffered).filter(
    (e) => !covered.has(e.event_id),
  );
  const next = { ...snapshot, events: mergeEvents(history, snapshot.events) };
  return newer.reduce(
    (state, event) =>
      applyEvent(
        { ...state, events: state.events.filter((e) => e.event_id !== event.event_id) },
        event,
      ),
    next,
  );
}
export function applyEvent(state: Snapshot, event: Event): Snapshot {
  if (
    event.project_id !== state.project.project_id ||
    state.events.some((e) => e.event_id === event.event_id)
  )
    return state;
  const statuses: Partial<Record<Event['event_type'], WorkflowNode['status']>> = {
    node_started: 'running',
    node_completed: 'succeeded',
    node_failed: 'failed',
    human_review_requested: 'waiting_human',
  };
  const status = statuses[event.event_type];
  const nodes = state.graph.nodes?.map((node) =>
    node.node_id === event.node_id
      ? {
          ...node,
          ...(status ? { status } : {}),
          ...(event.event_type === 'node_started' ? { started_at: event.timestamp } : {}),
          ...(event.event_type === 'node_completed'
            ? { completed_at: event.timestamp, progress: 1 }
            : {}),
          ...(event.event_type === 'node_progress' && typeof event.payload?.progress === 'number'
            ? { progress: event.payload.progress }
            : {}),
        }
      : node,
  );
  // Creation payloads are intentionally partial. The hook fetches the typed snapshot.
  return {
    ...state,
    graph: { ...state.graph, nodes },
    events: mergeEvents(state.events, [event]),
    provider_execution_graphs: applyProviderExecutionEvent(
      state.provider_execution_graphs || [],
      event,
    ),
    status:
      event.event_type === 'workflow_completed'
        ? 'completed'
        : event.event_type === 'human_review_requested'
          ? 'waiting_human'
          : state.status,
  };
}

const stageNames = ['Development', 'Cinematography', 'Production', 'Review & repair', 'Finishing'];
export function stageOf(node: WorkflowNode) {
  if (['shot_planning', 'shot_gate'].includes(node.node_id)) return 1;
  if (['asset_planning', 'storyboard_planning', 'shot_production'].includes(node.node_id)) return 2;
  if (
    ['technical_qc', 'visual_semantic_critic', 'cinematic_critic', 'repair_accept'].includes(
      node.node_id,
    )
  )
    return 3;
  if (
    ['audio_post', 'rough_cut', 'full_film_review', 'final_gate', 'final_render'].includes(
      node.node_id,
    )
  )
    return 4;
  return 0;
}
export function projectProgress(snapshot: Snapshot, previous = 0) {
  const nodes = snapshot.graph.nodes || [];
  const complete = nodes.filter((n) => n.status === 'succeeded').length;
  const stageFractions = stageNames.map((_, index) => {
    const group = nodes.filter((n) => stageOf(n) === index);
    return group.length
      ? group.reduce((sum, n) => sum + (n.status === 'succeeded' ? 1 : n.progress || 0), 0) /
          group.length
      : 0;
  });
  // Equal stage weights, conservative cap and a view-only high-water mark for graph growth.
  const value =
    snapshot.status === 'completed'
      ? 100
      : Math.max(previous, Math.min(99, stageFractions.reduce((a, b) => a + b, 0) * 20));
  const active = nodes.find((n) => n.status === 'running' || n.status === 'waiting_human');
  return {
    value,
    complete,
    total: nodes.length,
    stage:
      snapshot.status === 'completed'
        ? 'Workflow completed'
        : active?.label || snapshot.status.replaceAll('_', ' '),
  };
}
