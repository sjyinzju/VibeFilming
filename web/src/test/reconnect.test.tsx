import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { api, ApiError } from '../api/client';
import * as events from '../api/events';
import { useStudio } from '../state/useStudio';
import snapshotData from './snapshot.json';
import type { Snapshot } from '../api/types';

it('reconnects after cursor conflict via a new snapshot, then projects replay/live events', async () => {
  const base = structuredClone(snapshotData) as Snapshot;
  vi.spyOn(api, 'snapshot').mockResolvedValue(base);
  vi.spyOn(events, 'delay').mockResolvedValue(undefined);
  let calls = 0;
  const stream = vi
    .spyOn(events, 'readEvents')
    .mockImplementation(async (_pid, _cursor, signal, receive, connected) => {
      calls++;
      if (calls === 1) throw new ApiError(409, 'Unknown cursor');
      connected();
      receive({
        schema_version: '1.0.0',
        project_id: base.project.project_id,
        event_id: 'new-event',
        timestamp: new Date().toISOString(),
        trace_id: 'test',
        node_id: 'brief',
        job_id: null,
        event_type: 'node_started',
        payload: { status: 'running' },
      });
      await new Promise<void>((resolve) =>
        signal.addEventListener('abort', () => resolve(), { once: true }),
      );
    });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const { result, unmount } = renderHook(() => useStudio(base.project.project_id), { wrapper });
  await waitFor(() => expect(result.current.connection).toBe('Live'));
  expect(stream).toHaveBeenCalledTimes(2);
  expect(api.snapshot).toHaveBeenCalledTimes(3);
  expect(result.current.data?.graph.nodes[0].status).toBe('running');
  expect(stream.mock.calls[1][1]).toBe(base.event_cursor);
  unmount();
});
