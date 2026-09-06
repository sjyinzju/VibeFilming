import { t, useLocale } from '../i18n';
import { memo, useEffect, useMemo, useState } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  Panel,
  useNodesState,
  useReactFlow,
  ReactFlowProvider,
  type NodeProps,
} from '@xyflow/react';
import {
  Aperture,
  Camera,
  Clapperboard,
  Check,
  Circle,
  Loader,
  Hand,
  Layers,
  Flag,
  Maximize,
  LayoutGrid,
  RotateCcw,
  LocateFixed,
} from 'lucide-react';
import type { Snapshot } from '../api/types';
import { layoutNodes, projectCanvas, type StudioNode, type Positions } from '../state/canvas';
import { load, save } from '../state/storage';

const icons = {
  role: Aperture,
  scene: Clapperboard,
  shot: Camera,
  human: Hand,
  production: Layers,
  final: Flag,
};
const StudioCard = memo(function StudioCard({ data, selected }: NodeProps<StudioNode>) {
  useLocale();
  const Icon = icons[data.kind];
  return (
    <div className={`studio-node status-${data.status} ${selected ? 'selected' : ''}`}>
      <Handle type="target" position={Position.Top} />
      <div className="node-top">
        <Icon size={17} />
        <span>{t(data.kind === 'role' ? 'ROLE' : data.kind.toUpperCase())}</span>
        {data.status === 'succeeded' ? (
          <Check className="node-mark" size={15} />
        ) : data.status === 'running' ? (
          <Loader className="node-mark spin" size={14} />
        ) : (
          <Circle className="node-mark" size={7} />
        )}
      </div>
      <strong>{data.kind === 'scene' ? data.title : t(data.title)}</strong>
      <div className="node-subtitle">{t(data.subtitle)}</div>
      <div className="node-bottom">
        <span>{t(data.status === 'pending' ? 'Waiting' : data.status.replaceAll('_', ' '))}</span>
        <span>{t(data.metadata)}</span>
      </div>
      {data.media && (
        <div className="shot-status" aria-label={t('Shot media status')}>
          <span>
            {t('Frames')} {t(data.media.frames)}
          </span>
          <span>
            {t('Video')} {t(data.media.video)}
          </span>
          <span>
            {t('QC')} {t(data.media.qc)}
          </span>
        </div>
      )}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
});
const nodeTypes = {
  role: StudioCard,
  scene: StudioCard,
  shot: StudioCard,
  human: StudioCard,
  production: StudioCard,
  final: StudioCard,
};

type CanvasProps = {
  snapshot: Snapshot;
  onSelect: (id: string) => void;
  focusRequest?: { id: string };
};
function Canvas({ snapshot, onSelect, focusRequest }: CanvasProps) {
  useLocale();
  const pid = snapshot.project.project_id;
  const projected = useMemo(() => projectCanvas(snapshot), [snapshot]);
  const [positions] = useState<Positions>(() => load(`layout:${pid}`, {}));
  const [initial] = useState(() => layoutNodes(projected.nodes, projected.edges, positions));
  const [nodes, setNodes, onNodesChange] = useNodesState<StudioNode>(initial);
  const flow = useReactFlow<StudioNode>();
  useEffect(() => {
    if (!focusRequest) return;
    const target = flow.getNode(focusRequest.id);
    if (!target) return;
    setNodes((old) => old.map((n) => ({ ...n, selected: n.id === target.id })));
    void flow.setCenter(target.position.x + 115, target.position.y + 115, {
      zoom: 0.9,
      duration: 300,
    });
  }, [focusRequest, flow, setNodes]);
  useEffect(() => {
    setNodes((old) => {
      const known = Object.fromEntries(old.map((n) => [n.id, n.position]));
      return layoutNodes(projected.nodes, projected.edges, { ...positions, ...known }).map((n) => ({
        ...n,
        selected: old.find((o) => o.id === n.id)?.selected,
      }));
    });
  }, [projected, positions, setNodes]);
  const autoLayout = () => {
    const next = layoutNodes(projected.nodes, projected.edges);
    setNodes(next);
    save(`layout:${pid}`, Object.fromEntries(next.map((n) => [n.id, n.position])));
  };
  const focusNode =
    nodes.find((n) => ['running', 'waiting_human'].includes(n.data.status)) ||
    (snapshot.status === 'completed' ? nodes.find((n) => n.data.kind === 'final') : nodes[0]);
  const focus = () => {
    if (focusNode)
      void flow.setCenter(focusNode.position.x + 115, focusNode.position.y + 115, {
        zoom: 0.9,
        duration: 300,
      });
  };
  return (
    <ReactFlow<StudioNode>
      nodes={nodes}
      edges={projected.edges.map((edge) => ({
        ...edge,
        label: typeof edge.label === 'string' ? t(edge.label) : edge.label,
      }))}
      nodeTypes={nodeTypes}
      ariaLabelConfig={{
        'controls.zoomIn.ariaLabel': t('Zoom in'),
        'controls.zoomOut.ariaLabel': t('Zoom out'),
        'controls.fitView.ariaLabel': t('Fit view'),
      }}
      onNodesChange={onNodesChange}
      onNodeClick={(_, n) => onSelect(n.id)}
      onNodeDragStop={(_, node) =>
        save(
          `layout:${pid}`,
          Object.fromEntries(
            flow.getNodes().map((n) => [n.id, n.id === node.id ? node.position : n.position]),
          ),
        )
      }
      nodesConnectable={false}
      deleteKeyCode={null}
      minZoom={0.1}
      maxZoom={1.6}
      onInit={(instance) => {
        const viewport = load<null | { x: number; y: number; zoom: number }>(
          `viewport:${pid}`,
          null,
        );
        if (viewport) void instance.setViewport(viewport);
        else if (focusNode)
          void instance.setCenter(focusNode.position.x + 115, focusNode.position.y + 240, {
            zoom: 0.9,
          });
      }}
      onMoveEnd={(_, viewport) => save(`viewport:${pid}`, viewport)}
    >
      <Background gap={22} size={1} />
      <Controls showInteractive={false} />
      <MiniMap pannable zoomable nodeColor="var(--border)" maskColor="var(--minimap-mask)" />
      <Panel position="top-right" className="canvas-actions">
        <button
          onClick={focus}
          title={t('Focus current stage')}
          aria-label={t('Focus current stage')}
        >
          <LocateFixed size={15} />
        </button>
        <button onClick={autoLayout} title={t('Auto layout')} aria-label={t('Auto layout')}>
          <LayoutGrid size={15} />
        </button>
        <button
          onClick={() => flow.fitView({ duration: 300, maxZoom: 1 })}
          title={t('Fit view')}
          aria-label={t('Fit view')}
        >
          <Maximize size={15} />
        </button>
        <button
          onClick={() => flow.setViewport({ x: 40, y: 40, zoom: 1 }, { duration: 300 })}
          title={t('Reset view')}
          aria-label={t('Reset view')}
        >
          <RotateCcw size={15} />
        </button>
      </Panel>
    </ReactFlow>
  );
}
export function WorkflowCanvas(props: CanvasProps) {
  return (
    <ReactFlowProvider key={props.snapshot.project.project_id}>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}
