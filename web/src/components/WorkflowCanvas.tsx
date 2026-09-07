import { t, useLocale } from '../i18n';
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
  ChevronDown,
  ChevronRight,
  Cpu,
  XCircle,
} from 'lucide-react';
import type { ProviderExecutionGraph, Snapshot } from '../api/types';
import {
  layoutNodes,
  layoutProviderExecutionGraph,
  projectCanvas,
  type StudioNode,
  type Positions,
} from '../state/canvas';
import { load, save } from '../state/storage';

const icons = {
  role: Aperture,
  scene: Clapperboard,
  shot: Camera,
  human: Hand,
  production: Layers,
  final: Flag,
};

const executionNodeWidth = 168;
const executionNodeHeight = 68;

const ExecutionGraphPanel = memo(function ExecutionGraphPanel({
  graph,
}: {
  graph: ProviderExecutionGraph;
}) {
  const layout = useMemo(() => layoutProviderExecutionGraph(graph), [graph]);
  return (
    <section
      className="execution-graph-panel nodrag nowheel"
      aria-label="ComfyUI execution graph"
      onClick={(event) => event.stopPropagation()}
    >
      <header>
        <span>
          <Cpu size={13} /> {graph.provider}
        </span>
        <code>
          {graph.workflow_template_id}@{graph.workflow_template_version}
        </code>
      </header>
      <div className="execution-graph-scroll nowheel">
        <div
          className="execution-graph-canvas"
          style={{ width: layout.width, height: layout.height }}
        >
          <svg aria-hidden="true" width={layout.width} height={layout.height}>
            {graph.edges.map((edge) => {
              const source = layout.positions[edge.source];
              const target = layout.positions[edge.target];
              if (!source || !target) return null;
              const x1 = source.x + executionNodeWidth / 2;
              const y1 = source.y + executionNodeHeight;
              const x2 = target.x + executionNodeWidth / 2;
              const y2 = target.y;
              const mid = (y1 + y2) / 2;
              return (
                <path
                  key={edge.edge_id}
                  d={`M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`}
                />
              );
            })}
          </svg>
          {graph.nodes.map((node) => {
            const position = layout.positions[node.remote_node_id];
            const percent =
              node.progress_is_determinate && typeof node.progress === 'number'
                ? `${Math.round(node.progress * 100)}%`
                : null;
            return (
              <div
                key={node.remote_node_id}
                className={`execution-node execution-${node.runtime_state}`}
                style={{ left: position.x, top: position.y }}
                aria-label={`${node.display_label}: ${node.runtime_state}${percent ? ` ${percent}` : ''}`}
              >
                <div>
                  <span>{node.category}</span>
                  {node.runtime_state === 'succeeded' ? (
                    <Check size={12} />
                  ) : node.runtime_state === 'running' ? (
                    <Loader className="spin" size={12} />
                  ) : ['failed', 'interrupted'].includes(node.runtime_state) ? (
                    <XCircle size={12} />
                  ) : (
                    <Circle size={7} />
                  )}
                </div>
                <strong>{node.display_label}</strong>
                <small>{percent || node.class_type}</small>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
});

const StudioCard = memo(function StudioCard({ data, selected }: NodeProps<StudioNode>) {
  useLocale();
  const Icon = icons[data.kind];
  const execution = data.executionGraph;
  const executionExpanded = Boolean(data.executionExpanded);
  const runningCount =
    execution?.nodes.filter((node) => node.runtime_state === 'running').length || 0;
  return (
    <div
      className={`studio-node status-${data.status} ${selected ? 'selected' : ''} ${execution ? 'has-execution' : ''} ${executionExpanded ? 'expanded' : ''}`}
    >
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
      {execution && (
        <div className={`execution-summary ${runningCount ? 'is-running' : ''}`}>
          <button
            type="button"
            className="nodrag"
            aria-expanded={executionExpanded}
            aria-label={`${executionExpanded ? 'Collapse' : 'Expand'} ComfyUI execution graph`}
            onClick={(event) => {
              event.stopPropagation();
              data.onToggleExecution?.(execution.execution_id);
            }}
          >
            {executionExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            <Cpu size={12} />
            <span>ComfyUI execution</span>
            <small>
              {runningCount ? `${runningCount} running` : `${execution.nodes.length} nodes`}
            </small>
          </button>
        </div>
      )}
      {execution && executionExpanded && <ExecutionGraphPanel graph={execution} />}
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
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(load<string[]>(`execution-expanded:${pid}`, [])),
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const autoExpanded = useRef(new Set<string>());
  const toggleExecution = useCallback(
    (executionId: string) => {
      setExpanded((current) => {
        const next = new Set(current);
        if (next.has(executionId)) next.delete(executionId);
        else next.add(executionId);
        save(`execution-expanded:${pid}`, [...next]);
        return next;
      });
    },
    [pid],
  );
  const visibleNodes = useMemo(
    () =>
      projected.nodes.map((node) => ({
        ...node,
        data: {
          ...node.data,
          executionExpanded: Boolean(
            node.data.executionGraph && expanded.has(node.data.executionGraph.execution_id),
          ),
          onToggleExecution: toggleExecution,
        },
      })),
    [expanded, projected.nodes, toggleExecution],
  );
  const [positions] = useState<Positions>(() => load(`layout:${pid}`, {}));
  const [initial] = useState(() => layoutNodes(visibleNodes, projected.edges, positions));
  const [nodes, setNodes, onNodesChange] = useNodesState<StudioNode>(initial);
  const renderedNodes = useMemo(() => {
    const liveData = new Map(visibleNodes.map((node) => [node.id, node.data]));
    return nodes.map((node) => ({
      ...node,
      data: liveData.get(node.id) || node.data,
    }));
  }, [nodes, visibleNodes]);
  const flow = useReactFlow<StudioNode>();
  useEffect(() => {
    if (!focusRequest) return;
    const target = flow.getNode(focusRequest.id);
    if (!target) return;
    setNodes((old) => old.map((n) => ({ ...n, selected: n.id === target.id })));
    setSelectedId(target.id);
    void flow.setCenter(target.position.x + 115, target.position.y + 115, {
      zoom: 0.9,
      duration: 300,
    });
  }, [focusRequest, flow, setNodes]);
  useEffect(() => {
    setNodes((old) => {
      const known = Object.fromEntries(old.map((n) => [n.id, n.position]));
      return layoutNodes(visibleNodes, projected.edges, { ...positions, ...known }).map((n) => ({
        ...n,
        selected: old.find((o) => o.id === n.id)?.selected,
      }));
    });
  }, [visibleNodes, projected.edges, positions, setNodes]);
  useEffect(() => {
    if (!selectedId) return;
    const execution = (snapshot.provider_execution_graphs || []).find(
      (graph) =>
        graph.parent_node_id === selectedId &&
        graph.nodes.some((node) => node.runtime_state === 'running'),
    );
    if (!execution || autoExpanded.current.has(execution.execution_id)) return;
    autoExpanded.current.add(execution.execution_id);
    setExpanded((current) => {
      const next = new Set(current).add(execution.execution_id);
      save(`execution-expanded:${pid}`, [...next]);
      return next;
    });
  }, [pid, selectedId, snapshot.provider_execution_graphs]);
  const autoLayout = () => {
    const next = layoutNodes(visibleNodes, projected.edges);
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
      nodes={renderedNodes}
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
      onNodeClick={(_, n) => {
        setSelectedId(n.id);
        onSelect(n.id);
      }}
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
