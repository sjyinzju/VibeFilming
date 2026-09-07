import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { WorkflowCanvas } from '../components/WorkflowCanvas';
import type { ProviderExecutionGraph, Snapshot } from '../api/types';
import { layoutProviderExecutionGraph, projectCanvas } from '../state/canvas';
import snapshotData from './snapshot.json';

afterEach(cleanup);

function executionGraph(
  count = 3,
  state: 'waiting' | 'running' | 'succeeded' | 'failed' = 'running',
) {
  const nodes = Array.from({ length: count }, (_, index) => ({
    schema_version: '1.0.0',
    remote_node_id: `node-${index}`,
    class_type: index === 1 ? 'SamplerCustomAdvanced' : 'FixtureNode',
    display_label: index === 1 ? 'Sampling' : `Node ${index}`,
    category: index === 1 ? 'sampling' : 'input',
    runtime_state: index === 1 ? state : state === 'succeeded' ? 'succeeded' : 'waiting',
    progress: index === 1 && state === 'running' ? 0.43 : null,
    progress_is_determinate: index === 1 && state === 'running',
    started_at: null,
    completed_at: state === 'succeeded' ? new Date().toISOString() : null,
    error: index === 1 && state === 'failed' ? 'fixture failure' : null,
    input_summary: [],
    output_artifact_ids: [],
  }));
  return {
    schema_version: '1.0.0',
    execution_id: 'prompt-1',
    provider: 'comfyui-video',
    workflow_template_id: 'comfy_org_minimax_h3_fl2va',
    workflow_template_version: '1.0.0',
    workflow_template_hash: 'a'.repeat(64),
    binding_manifest_id: 'bindings',
    binding_manifest_version: '1.0.0',
    parent_node_id: 'shot_production',
    parent_job_id: 'job-1',
    project_id: snapshotData.project.project_id,
    scene_id: null,
    shot_id: null,
    remote_prompt_id: 'prompt-1',
    nodes,
    edges: nodes.slice(1).map((node, index) => ({
      schema_version: '1.0.0',
      edge_id: `edge-${index}`,
      source: nodes[index].remote_node_id,
      target: node.remote_node_id,
      source_slot: 0,
      target_slot: 'input',
    })),
    created_at: new Date().toISOString(),
    completed_at: state === 'succeeded' ? new Date().toISOString() : null,
  } as unknown as ProviderExecutionGraph;
}

function snapshot(graph?: ProviderExecutionGraph) {
  const value = structuredClone(snapshotData) as Snapshot;
  value.provider_execution_graphs = graph ? [graph] : [];
  return value;
}

function executionButton(container: HTMLElement) {
  return container.querySelector<HTMLButtonElement>(
    '[data-testid="rf__node-shot_production"] .execution-summary button',
  );
}

it('lazy-renders a collapsed graph, expands real nodes with real progress, and collapses again', async () => {
  const graph = executionGraph();
  expect(
    projectCanvas(snapshot(graph)).nodes.find((node) => node.id === 'shot_production')?.data
      .executionGraph,
  ).toBeDefined();
  const { container, rerender } = render(
    <WorkflowCanvas snapshot={snapshot(graph)} onSelect={() => {}} />,
  );
  expect(screen.queryByLabelText('ComfyUI execution graph')).not.toBeInTheDocument();
  expect(container.querySelectorAll('.execution-node')).toHaveLength(0);
  fireEvent.click(screen.getByText('Shot Production', { exact: true }));
  await screen.findByLabelText('ComfyUI execution graph');
  let toggle = executionButton(container);
  expect(toggle).toHaveAttribute('aria-label', 'Collapse ComfyUI execution graph');
  fireEvent.click(toggle!);
  const expand = executionButton(container);
  expect(expand).toHaveAttribute('aria-label', 'Expand ComfyUI execution graph');
  fireEvent.click(expand!);
  expect(screen.getByLabelText('ComfyUI execution graph')).toBeInTheDocument();
  expect(screen.getByLabelText('Sampling: running 43%')).toBeInTheDocument();

  rerender(
    <WorkflowCanvas snapshot={snapshot(executionGraph(3, 'succeeded'))} onSelect={() => {}} />,
  );
  expect(screen.getByLabelText('Sampling: succeeded')).toBeInTheDocument();
  toggle = executionButton(container);
  expect(toggle).toHaveAttribute('aria-label', 'Collapse ComfyUI execution graph');
  fireEvent.click(toggle!);
  expect(screen.queryByLabelText('ComfyUI execution graph')).not.toBeInTheDocument();
});

it('auto-expands once when a selected video production node starts running', async () => {
  render(<WorkflowCanvas snapshot={snapshot(executionGraph())} onSelect={() => {}} />);
  fireEvent.click(screen.getByText('Shot Production', { exact: true }));
  await waitFor(() => expect(screen.getByLabelText('ComfyUI execution graph')).toBeInTheDocument());
});

it('does not expose execution controls for a direct provider and keeps large graphs lazy', async () => {
  const direct = render(<WorkflowCanvas snapshot={snapshot()} onSelect={() => {}} />);
  expect(executionButton(direct.container)).not.toBeInTheDocument();
  direct.unmount();

  const large = render(
    <WorkflowCanvas snapshot={snapshot(executionGraph(100, 'waiting'))} onSelect={() => {}} />,
  );
  expect(large.container.querySelectorAll('.execution-node')).toHaveLength(0);
  fireEvent.click(within(large.container).getByText('Shot Production', { exact: true }));
  await waitFor(() => expect(executionButton(large.container)).toBeInTheDocument());
  fireEvent.click(executionButton(large.container)!);
  expect(large.container.querySelectorAll('.execution-node')).toHaveLength(100);
});

it('renders a failed provider node without fabricating progress', async () => {
  const { container } = render(
    <WorkflowCanvas snapshot={snapshot(executionGraph(3, 'failed'))} onSelect={() => {}} />,
  );
  fireEvent.click(screen.getByText('Shot Production', { exact: true }));
  await waitFor(() => expect(executionButton(container)).toBeInTheDocument());
  fireEvent.click(executionButton(container)!);
  expect(screen.getByLabelText('Sampling: failed')).toBeInTheDocument();
  expect(screen.queryByLabelText(/Sampling: failed .*%/)).not.toBeInTheDocument();
});

it('lays out the provider graph deterministically without ComfyUI GUI coordinates', () => {
  const graph = executionGraph(8, 'waiting');
  expect(layoutProviderExecutionGraph(graph)).toEqual(layoutProviderExecutionGraph(graph));
  expect(Object.keys(layoutProviderExecutionGraph(graph).positions)).toHaveLength(8);
});
