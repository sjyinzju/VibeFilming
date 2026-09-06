import type { CreateInput, Schema, Snapshot } from './types';

export const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '');
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    });
  } catch {
    throw new ApiError(0, 'Cannot reach Movie Agent. Check that the backend is running.');
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail =
      typeof body?.detail === 'string'
        ? body.detail
        : response.status === 422
          ? 'Some fields are invalid. Check your brief.'
          : 'The request could not be completed.';
    throw new ApiError(response.status, detail);
  }
  return response.json();
}
const id = encodeURIComponent;
export const api = {
  projects: () => request<Schema['ProjectRecord'][]>('/projects'),
  create: (brief: CreateInput) =>
    request<Schema['ProjectRecord']>('/projects', { method: 'POST', body: JSON.stringify(brief) }),
  snapshot: (pid: string) => request<Snapshot>(`/projects/${id(pid)}/studio`),
  command: (pid: string, action: 'start' | 'pause' | 'resume' | 'cancel') =>
    request<Schema['CommandAccepted']>(`/projects/${id(pid)}/${action}`, { method: 'POST' }),
  resolve: (rid: string, approved: boolean, notes: string) =>
    request<Schema['HumanReviewRequest']>(`/reviews/${id(rid)}/resolve`, {
      method: 'POST',
      body: JSON.stringify({ approved, notes }),
    }),
  providers: () => request<Schema['ProviderView'][]>('/providers'),
  job: (pid: string, jid: string) =>
    request<Schema['GenerationJob']>(`/jobs/${id(jid)}?project_id=${id(pid)}`),
  artifact: (pid: string, aid: string, version: number) =>
    request<Schema['Artifact']>(`/artifacts/${id(aid)}?project_id=${id(pid)}&version=${version}`),
};
