import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../api/client';
import { readEvents, delay } from '../api/events';
import type { Event, Snapshot } from '../api/types';
import { applyEvent, reconcileSnapshot } from './workflow';

export function useStudio(pid: string | null) {
  const client = useQueryClient();
  const [connection, setConnection] = useState('Offline');
  const [recovered, setRecovered] = useState(0);
  const query = useQuery({
    queryKey: ['studio', pid],
    queryFn: () => api.snapshot(pid!),
    enabled: !!pid,
    retry: false,
    staleTime: Infinity,
  });
  useEffect(() => {
    if (!pid) {
      setConnection('Offline');
      return;
    }
    const controller = new AbortController();
    let activeStream: AbortController | undefined;
    const offline = () => {
      setConnection('Offline');
      activeStream?.abort();
    };
    window.addEventListener('offline', offline);
    let cursor: string | null | undefined;
    let seen = new Set<string>();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let pending = false,
      refreshing = false;
    let duringRefresh: Event[] = [];
    const key = ['studio', pid];
    const refresh = async () => {
      if (refreshing) {
        pending = true;
        return;
      }
      refreshing = true;
      duringRefresh = [];
      try {
        const snapshot = await api.snapshot(pid);
        if (!controller.signal.aborted)
          client.setQueryData<Snapshot>(key, (old) =>
            reconcileSnapshot(snapshot, old?.events || [], duringRefresh),
          );
      } catch {
        /* Stream remains usable; next refresh and reconnect retry. */
      } finally {
        refreshing = false;
        if (pending && !controller.signal.aborted) {
          pending = false;
          schedule();
        }
      }
    };
    const schedule = () => {
      if (!timer)
        timer = setTimeout(() => {
          timer = undefined;
          void refresh();
        }, 180);
    };
    const receive = (event: Event) => {
      cursor = event.event_id;
      if (seen.has(event.event_id)) return;
      seen.add(event.event_id);
      if (seen.size > 2000) seen = new Set([...seen].slice(-1000));
      if (refreshing) duringRefresh.push(event);
      client.setQueryData<Snapshot>(key, (old) => (old ? applyEvent(old, event) : old));
      schedule();
    };
    void (async () => {
      let failures = 0,
        reconnecting = false;
      while (!controller.signal.aborted) {
        try {
          if (cursor === undefined) {
            const snapshot = await client.fetchQuery({
              queryKey: key,
              queryFn: () => api.snapshot(pid),
              staleTime: 0,
            });
            if (controller.signal.aborted) return;
            cursor = snapshot.event_cursor;
            seen = new Set(snapshot.events.map((e) => e.event_id));
          }
          let replayCount = 0;
          const recoveryCursor = reconnecting ? (await api.snapshot(pid)).event_cursor : null;
          if (recoveryCursor === cursor) reconnecting = false;
          activeStream = new AbortController();
          await readEvents(
            pid,
            cursor,
            AbortSignal.any([controller.signal, activeStream.signal]),
            (event) => {
              if (reconnecting) {
                if (!seen.has(event.event_id)) {
                  replayCount++;
                  setRecovered(replayCount);
                }
                if (event.event_id === recoveryCursor) reconnecting = false;
              }
              receive(event);
            },
            () => {
              failures = 0;
              setConnection('Live');
            },
          );
        } catch (error) {
          if (controller.signal.aborted) return;
          if (error instanceof ApiError && error.status === 409) cursor = undefined;
          failures++;
          reconnecting = true;
          setConnection(!navigator.onLine || failures > 3 ? 'Offline' : 'Reconnecting');
          await delay(Math.min(1000 * 2 ** (failures - 1), 10000), controller.signal);
        }
      }
    })();
    // Lifecycle acknowledgements may settle just after the final event. Read-only reconciliation.
    const reconciliation = setInterval(() => {
      void refresh();
    }, 5000);
    return () => {
      controller.abort();
      window.removeEventListener('offline', offline);
      clearTimeout(timer);
      clearInterval(reconciliation);
    };
  }, [pid, client]);
  return {
    ...query,
    connection,
    recovered,
    refresh: () => client.invalidateQueries({ queryKey: ['studio', pid] }),
  };
}
