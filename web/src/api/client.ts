import type {
  CreateInput,
  AudioProductionInput,
  ImageReferenceUpload,
  ReferenceBindingInput,
  Schema,
  Snapshot,
} from './types';

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
  if (response.status === 204) return undefined as T;
  return response.json();
}
async function upload<T>(path: string, file: File, binding: unknown): Promise<T> {
  const body = new FormData();
  body.append('file', file, file.name);
  body.append('binding', JSON.stringify(binding));
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { method: 'POST', body });
  } catch {
    throw new ApiError(0, 'Cannot reach Movie Agent. Check that the backend is running.');
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(response.status, payload?.detail || 'Image upload failed.');
  }
  return response.json();
}
const id = encodeURIComponent;
export const api = {
  projects: () => request<Schema['ProjectRecord'][]>('/projects'),
  create: (brief: CreateInput) =>
    request<Schema['ProjectRecord']>('/projects', { method: 'POST', body: JSON.stringify(brief) }),
  snapshot: (pid: string) => request<Snapshot>(`/projects/${id(pid)}/studio`),
  audio: (pid: string, command: AudioProductionInput) =>
    request<Schema['CommandAccepted']>(`/projects/${id(pid)}/audio`, {
      method: 'POST',
      body: JSON.stringify(command),
    }),
  command: (pid: string, action: 'start' | 'pause' | 'resume' | 'cancel') =>
    request<Schema['CommandAccepted']>(`/projects/${id(pid)}/${action}`, { method: 'POST' }),
  reviseTerminal: (pid: string, sceneId: string) =>
    request<Schema['CommandAccepted']>(`/projects/${id(pid)}/revise-terminal`, {
      method: 'POST',
      body: JSON.stringify({
        scene_id: sceneId,
        authorization_reference:
          'Explicit Web Studio Correct planning / Replan action: one semantic revision + at most two repairs',
      }),
    }),
  resolve: (
    rid: string,
    approved: boolean,
    notes: string,
    media_directive?: Schema['HumanRepairInput'],
  ) =>
    request<Schema['HumanReviewRequest']>(`/reviews/${id(rid)}/resolve`, {
      method: 'POST',
      body: JSON.stringify({ approved, notes, media_directive }),
    }),
  mediaFeedback: (pid: string, directive: Schema['HumanRepairInput']) =>
    request<Schema['HumanReviewRequest']>(`/projects/${id(pid)}/media-feedback`, {
      method: 'POST',
      body: JSON.stringify(directive),
    }),
  providers: () => request<Schema['ProviderView'][]>('/providers'),
  job: (pid: string, jid: string) =>
    request<Schema['GenerationJob']>(`/jobs/${id(jid)}?project_id=${id(pid)}`),
  artifact: (pid: string, aid: string, version: number) =>
    request<Schema['Artifact']>(`/artifacts/${id(aid)}?project_id=${id(pid)}&version=${version}`),
  selectArtifact: (pid: string, aid: string, version: number) =>
    request<Schema['Artifact']>(
      `/artifacts/${id(aid)}/select?project_id=${id(pid)}&version=${version}`,
      { method: 'POST' },
    ),
  uploadDraftReference: (draftId: string, file: File, binding: ReferenceBindingInput) =>
    upload<ImageReferenceUpload>(`/drafts/${id(draftId)}/image-references`, file, binding),
  uploadProjectReference: (pid: string, file: File, binding: ReferenceBindingInput) =>
    upload<ImageReferenceUpload>(`/projects/${id(pid)}/image-references`, file, binding),
  removeDraftReference: (draftId: string, referenceId: string) =>
    request<void>(`/drafts/${id(draftId)}/image-references/${id(referenceId)}`, {
      method: 'DELETE',
    }),
  removeProjectReference: (pid: string, referenceId: string) =>
    request<void>(`/projects/${id(pid)}/image-references/${id(referenceId)}`, {
      method: 'DELETE',
    }),
};
