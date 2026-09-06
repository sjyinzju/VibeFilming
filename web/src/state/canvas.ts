import dagre from '@dagrejs/dagre';
import type { Edge, Node, XYPosition } from '@xyflow/react';
import type { Snapshot } from '../api/types';

export type CanvasKind = 'role' | 'scene' | 'shot' | 'human' | 'production' | 'final';
export type CanvasData = {
  title: string;
  subtitle: string;
  status: string;
  kind: CanvasKind;
  identity: string;
  metadata?: string;
};
export type StudioNode = Node<CanvasData>;
export type Positions = Record<string, XYPosition>;
export function projectCanvas(snapshot: Snapshot) {
  const nodes: StudioNode[] = (snapshot.graph.nodes || []).map((n) => {
    const kind: CanvasKind =
      n.node_type === 'human_gate'
        ? 'human'
        : n.node_type === 'delivery'
          ? 'final'
          : ['production', 'assets', 'storyboard', 'quality', 'repair', 'post'].includes(
                n.node_type,
              )
            ? 'production'
            : 'role';
    return {
      id: n.node_id,
      type: kind,
      position: { x: 0, y: 0 },
      data: {
        title: n.label,
        subtitle: n.role,
        status: n.status || 'pending',
        kind,
        identity: n.node_id,
        metadata: n.group,
      },
    };
  });
  const status = new Map(nodes.map((n) => [n.id, n.data.status]));
  const edges: Edge[] = (snapshot.graph.edges || []).map((e) => ({
    id: e.edge_id,
    source: e.source_node_id,
    target: e.target_node_id,
    label:
      e.label ||
      (['repair', 'failure', 'human_gate'].includes(e.edge_type || '')
        ? e.edge_type?.replaceAll('_', ' ')
        : undefined),
    animated: status.get(e.target_node_id) === 'running',
    className: `edge-${e.edge_type} ${status.get(e.target_node_id) === 'running' ? 'edge-active' : ''}`,
    type: 'smoothstep',
  }));
  (snapshot.project.scenes || []).forEach((scene, index) => {
    const id = `scene:${scene.scene_id}`;
    const shots = snapshot.project.shots?.filter((s) => s.scene_id === scene.scene_id) || [];
    nodes.push({
      id,
      type: 'scene',
      position: { x: 0, y: 0 },
      data: {
        kind: 'scene',
        identity: scene.scene_id,
        title: scene.title,
        subtitle: `SCENE ${String(index + 1).padStart(2, '0')}`,
        status: 'planned',
        metadata: `${shots.length} shots · ${shots.reduce((n, s) => n + s.duration_seconds, 0)}s planned`,
      },
    });
    if (status.has('scene_planning'))
      edges.push({
        id: `projection:${id}`,
        source: 'scene_planning',
        target: id,
        type: 'smoothstep',
        className: 'edge-projection',
        label: 'scene plan',
      });
    shots.forEach((shot, i) => {
      const sid = `shot:${shot.shot_id}`;
      nodes.push({
        id: sid,
        type: 'shot',
        position: { x: 0, y: 0 },
        data: {
          kind: 'shot',
          identity: shot.shot_id,
          title: `SHOT ${String(index + 1).padStart(2, '0')}-${String(i + 1).padStart(2, '0')}`,
          subtitle: `${shot.camera.shot_size.replaceAll('_', ' ')}${shot.camera.lens_mm ? ` · ${shot.camera.lens_mm}mm` : ''}`,
          status: 'planned',
          metadata: `${shot.camera.motion?.motion_type || 'static'} · ${shot.duration_seconds}s`,
        },
      });
      edges.push({
        id: `projection:${sid}`,
        source: id,
        target: sid,
        type: 'smoothstep',
        className: 'edge-projection',
      });
    });
  });
  return { nodes, edges };
}

export function layoutNodes(
  nodes: StudioNode[],
  edges: Edge[],
  previous: Positions = {},
): StudioNode[] {
  if (nodes.every((n) => previous[n.id]))
    return nodes.map((n) => ({ ...n, position: previous[n.id] }));
  const graph = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  graph.setGraph({ rankdir: 'TB', nodesep: 50, ranksep: 64, marginx: 40, marginy: 40 });
  nodes.forEach((n) => graph.setNode(n.id, { width: 230, height: 112 }));
  edges.forEach((e) => graph.setEdge(e.source, e.target));
  dagre.layout(graph);
  const occupied = nodes.filter((n) => previous[n.id]).map((n) => previous[n.id]);
  return nodes.map((n) => {
    if (previous[n.id]) return { ...n, position: previous[n.id] };
    const point = graph.node(n.id);
    const position = { x: point.x - 115, y: point.y - 56 };
    while (
      occupied.some((p) => Math.abs(p.x - position.x) < 245 && Math.abs(p.y - position.y) < 125)
    )
      position.x += 280;
    occupied.push(position);
    return { ...n, position };
  });
}
