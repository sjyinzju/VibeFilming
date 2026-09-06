# Future Frontend Contract

P2B implementation: [Web Studio](p2b_frontend.md). The design below is retained as the original boundary specification. The implemented Studio consumes the real P2A APIs with compatible application-level creative input, project listing, aggregate Inspector snapshot and local cancellation extensions.

## Scope

The core does not include a web application. It already exposes the stable data a future input console, workflow canvas, inspector, approval panel, and artifact/version browser need.

Phase 2A now supplies the FastAPI backend and SSE transport. P2B can directly consume the complete endpoint list in `p2a_runtime.md`; no frontend has been implemented. OpenAPI documentation is served by FastAPI at `/docs` and `/openapi.json`.

## Input console

Render `ProjectBrief.model_json_schema()` into sections matching basic, story, characters, world, visual, cinematography, audio, and production. Preserve the semantic distinction between:

- hard `user_constraints` / `must_preserve` fields;
- the selected `creative_freedom` level.

Do not store frontend-only control state in the brief.

## Workflow canvas projection

The frontend should keep a local projection keyed by stable IDs:

| Event family | Projection update |
|---|---|
| project | create project header/session |
| node | add node; update status/progress/timestamps |
| edge | add/activate directed edge |
| job | add job; update execution status/progress/failure |
| artifact | add version; switch selected version |
| evaluation | attach scores/issues/evidence to shot/artifact |
| repair | show repair plan, attempt, and outcome |
| human review | open/resolve approval panel |
| workflow completed | expose final artifact action |

The canvas computes coordinates and layout. Backend nodes supply hierarchy, group, dependencies, children, ordering, role, status, and semantic icon/color hints only.

## Read models

Recommended frontend read models are projections, not new source-of-truth domain objects:

- project summary from `Project`;
- workflow view from `WorkflowGraph` plus recent events;
- shot inspector from `Shot`, current continuity state, evaluations, and artifact versions;
- provenance inspector from `Artifact.provenance` and parent IDs;
- job monitor from `GenerationJob`;
- approval queue from pending `HumanReviewRequest` records.

## Future transport boundary

A server adapter may expose REST for snapshots/commands and a streamed event transport for changes. Commands should refer to stable IDs and validate through domain contracts. Example commands include submit brief, cancel job, resolve review, select artifact version, request retry, and resume project.

The implemented adapter supports project creation/start/pause/resume, review resolution and job cancellation, plus project/workflow/shot/job/artifact reads. Version selection and arbitrary revision commands are not exposed over REST yet. Use graph/project snapshot `event_cursor` with `Last-Event-ID` on the SSE endpoint. Unknown cursors return 409. Events survive restart; `follow=false` provides a finite replay for clients/tests.

Transport acknowledgements must not be treated as production completion; completion is represented by persisted job/node state and events. Reconnection should fetch a current graph/project snapshot, then continue from the event cursor. The current `LocalEventBus` can be replaced without changing event payload contracts.

## Explicitly not specified here

Authentication, multi-tenancy, CSS, node coordinates, framework choice, browser caching, and server deployment are intentionally deferred.
