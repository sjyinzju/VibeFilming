import type { components } from './generated';
// FastAPI response_model serialization emits defaults (exclude_unset is false).
// Derive that output view without duplicating any backend fields or enum vocabulary.
type Serialized<T> = T extends (infer U)[]
  ? Serialized<U>[]
  : T extends object
    ? { [K in keyof T]-?: Serialized<Exclude<T[K], undefined>> }
    : T;
export type Schema = { [K in keyof components['schemas']]: Serialized<components['schemas'][K]> };
export type Brief = Schema['ProjectBrief'];
export type CreateInput = components['schemas']['CreateProjectInput'];
export type Hints = components['schemas']['CreativeHints'];
export type Snapshot = Schema['StudioSnapshot'];
export type WorkflowNode = Schema['WorkflowNode'];
export type WorkflowEdge = Schema['WorkflowEdge'];
export type Event = Schema['EventEnvelope'];
export type Review = Schema['HumanReviewRequest'];
export type Artifact = Schema['Artifact'];
export type MediaReferenceInput = components['schemas']['MediaReference'];
export type ReferenceBindingInput = components['schemas']['ImageReferenceBindingInput'];
export type ImageReferenceUpload = components['schemas']['ImageReferenceUploadResult'];
