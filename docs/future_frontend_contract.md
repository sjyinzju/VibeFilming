# Future Frontend Contract

## Scope

The core does not include a web application. It already exposes the stable data a future input console, workflow canvas, inspector, approval panel, and artifact/version browser need.

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

Transport acknowledgements must not be treated as production completion; completion is represented by persisted job/node state and events. Reconnection should fetch a current graph/project snapshot, then continue from the event cursor. The current `LocalEventBus` can be replaced without changing event payload contracts.

## Explicitly not specified here

Authentication, multi-tenancy, CSS, node coordinates, framework choice, browser caching, and server deployment are intentionally deferred.
