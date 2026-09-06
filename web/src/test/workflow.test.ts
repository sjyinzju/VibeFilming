import { describe, expect, it, vi } from 'vitest';
import snapshotData from './snapshot.json';
import type { Snapshot, Event } from '../api/types';
import { applyEvent, projectProgress, reconcileSnapshot } from '../state/workflow';
import { layoutNodes, projectCanvas } from '../state/canvas';
import { api, ApiError } from '../api/client';
import { readEvents } from '../api/events';
import { load, save } from '../state/storage';

export const fixture = () => structuredClone(snapshotData) as Snapshot;
export const event = (type: Event['event_type'], payload: Event['payload'] = {}): Event => ({
  schema_version: '1.0.0',
  event_id: `event-${type}`,
  event_type: type,
  project_id: snapshotData.project.project_id,
  timestamp: new Date().toISOString(),
  trace_id: 'test',
  node_id: 'brief',
  job_id: null,
  payload,
});

describe('workflow projection', () => {
  it('does not regress a completed snapshot with an already-covered in-flight started event', () => {
    const snapshot = fixture();
    const started = event('node_started');
    const completed = event('node_completed');
    snapshot.graph.nodes[0].status = 'succeeded';
    snapshot.events.push(started, completed);
    snapshot.event_cursor = completed.event_id;
    expect(reconcileSnapshot(snapshot, [started], [started]).graph.nodes[0].status).toBe(
      'succeeded',
    );
    const failed = event('node_failed');
    expect(reconcileSnapshot(snapshot, [], [completed, failed]).graph.nodes[0].status).toBe(
      'failed',
    );
  });
  it('maps actual snapshot identities and graph edges', () => {
    const snapshot = fixture();
    const canvas = projectCanvas(snapshot);
    expect(canvas.nodes.map((n) => n.id)).toEqual(snapshot.graph.nodes.map((n) => n.node_id));
    expect(canvas.edges).toHaveLength(20);
    expect(canvas.nodes.find((n) => n.id === 'story_gate')?.type).toBe('human');
  });
  it('projects SSE status and progress idempotently', () => {
    const initial = fixture();
    const started = event('node_started');
    const running = applyEvent(initial, started);
    expect(running.graph.nodes[0].status).toBe('running');
    expect(applyEvent(running, started)).toBe(running);
    const progress = applyEvent(running, event('node_progress', { progress: 0.4 }));
    expect(progress.graph.nodes[0].progress).toBe(0.4);
    expect(applyEvent(progress, event('node_completed')).graph.nodes[0].progress).toBe(1);
    expect(applyEvent(initial, { ...started, project_id: 'another' })).toBe(initial);
  });
  it('waits for typed snapshot to complete partial creation events', () => {
    const initial = fixture();
    const updated = applyEvent(initial, {
      ...event('node_created', { label: 'New role' }),
      node_id: 'new-role',
    });
    expect(updated.graph.nodes).toHaveLength(21);
    updated.graph.nodes.push({ ...initial.graph.nodes[0], node_id: 'new-role', label: 'New role' });
    expect(projectCanvas(updated).nodes).toHaveLength(22);
  });
  it('preserves dragged nodes as new nodes arrive and across local reload', () => {
    const initial = projectCanvas(fixture());
    const previous = { brief: { x: 912, y: 812 } };
    const next = layoutNodes(
      [...initial.nodes, { ...initial.nodes[0], id: 'new' }],
      initial.edges,
      previous,
    );
    expect(next[0].position).toEqual(previous.brief);
    expect(next.find((n) => n.id === 'new')?.position).not.toEqual(previous.brief);
    save('layout:test', previous);
    expect(load('layout:test', {})).toEqual(previous);
  });
  it('stage projection does not regress on graph growth and only completes on workflow completion', () => {
    const snapshot = fixture();
    snapshot.graph.nodes[0].status = 'succeeded';
    const progress = projectProgress(snapshot);
    expect(progress.value).toBeGreaterThan(0);
    snapshot.graph.nodes.push({ ...snapshot.graph.nodes[0], node_id: 'extra', status: 'pending' });
    expect(projectProgress(snapshot, progress.value).value).toBe(progress.value);
    snapshot.graph.nodes.forEach((n) => (n.status = 'succeeded'));
    expect(projectProgress(snapshot).value).toBe(99);
    snapshot.status = 'completed';
    expect(projectProgress(snapshot).value).toBe(100);
  });
});

describe('HTTP and durable SSE', () => {
  it('submits project input through the unified backend client', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ project: fixture().project }), { status: 201 }),
      );
    vi.stubGlobal('fetch', fetch);
    const result = await api.create({ story_description: 'A signal.' });
    expect(result.project.project_id).toBe(snapshotData.project.project_id);
    expect(fetch.mock.calls[0][0]).toBe('/api/projects');
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ story_description: 'A signal.' });
  });
  it('surfaces validation, conflict and network errors without tracebacks', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify({ detail: 'Production is terminal' }), { status: 409 }),
        ),
    );
    await expect(api.command('project_x', 'resume')).rejects.toThrow('Production is terminal');
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new Error('python traceback or socket internals')),
    );
    await expect(api.projects()).rejects.toThrow('Cannot reach Movie Agent');
  });
  it('parses chunked Unicode events and resumes using Last-Event-ID', async () => {
    const received: Event[] = [];
    const data = `: heartbeat\n\nid: e\nevent: node_progress\ndata: ${JSON.stringify(event('node_progress', { label: '场景', progress: 0.2 }))}\n\n`;
    const bytes = new TextEncoder().encode(data);
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(bytes.slice(0, 46));
        controller.enqueue(bytes.slice(46));
        controller.close();
      },
    });
    const fetch = vi.fn().mockResolvedValue(new Response(stream));
    vi.stubGlobal('fetch', fetch);
    await expect(
      readEvents(
        'project_test',
        'last-known',
        new AbortController().signal,
        (e) => received.push(e),
        () => {},
      ),
    ).rejects.toThrow('Stream closed');
    expect(received[0].payload.label).toBe('场景');
    expect(fetch.mock.calls[0][1].headers).toEqual({ 'Last-Event-ID': 'last-known' });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 409 })));
    await expect(
      readEvents(
        'project_test',
        'expired',
        new AbortController().signal,
        () => {},
        () => {},
      ),
    ).rejects.toBeInstanceOf(ApiError);
  });
});
