import { API_BASE, ApiError } from './client';
import type { Event } from './types';

/** Fetch SSE supports explicit Last-Event-ID, HTTP 409 recovery and AbortSignal. */
export async function readEvents(
  pid: string,
  cursor: string | null | undefined,
  signal: AbortSignal,
  receive: (event: Event) => void,
  connected: () => void,
) {
  const response = await fetch(`${API_BASE}/projects/${encodeURIComponent(pid)}/events`, {
    headers: cursor ? { 'Last-Event-ID': cursor } : {},
    signal,
  });
  if (!response.ok) throw new ApiError(response.status, 'Event stream unavailable');
  if (!response.body) throw new Error('Streaming is unavailable');
  connected();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) throw new Error('Stream closed');
      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, '\n');
      let boundary: number;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = block
          .split('\n')
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart())
          .join('\n');
        if (data) receive(JSON.parse(data) as Event);
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export function delay(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve) => {
    if (signal.aborted) return resolve();
    const finish = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', finish);
      resolve();
    };
    const timer = setTimeout(finish, ms);
    signal.addEventListener('abort', finish, { once: true });
  });
}
