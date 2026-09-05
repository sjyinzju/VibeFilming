# Phase 2A Runtime and Backend Spine

Phase 2A adds real reasoning on an external OpenAI-compatible endpoint. Images, frames, videos, visual critics, audio and final rendering remain Phase 1 mocks.

Phase 2A is complete and frozen. The accepted run and its exact checkpoint, validation, metrics, and integrity evidence are recorded in `p2a_verification.md`. The REST/SSE surface below is the handoff boundary for P2B; it was not extended with a frontend in this phase.

## Install and run

Use Python 3.12 or newer in a project virtual environment. Configure `.env` using `.env.example`; do not commit `.env`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/run_p2a.py --workspace workspace/p2a-demo
```

The demo input defaults to `tests/fixtures/sample_brief.json`. It contains an 18-second film brief. All six reasoning roles are real by default. `--through-role creative_producer` (or any later role) selects a prefix of real roles for incremental testing, with later creative stages supplied by deterministic mocks.

For a new workspace use a new run; for an existing production use `--resume`. The saved role configuration is authoritative on resume. The demo does not silently use mocks when the endpoint is unavailable.

```powershell
.\.venv\Scripts\python.exe scripts/run_p2a.py --workspace workspace/p2a-resume --stop-after-node bibles
.\.venv\Scripts\python.exe scripts/run_p2a.py --workspace workspace/p2a-resume --resume
```

The first command stops before Director; the second restores prior outputs and only invokes remaining roles. Checkpoints, immutable artifacts and event files are under the selected workspace. `.placeholder` media files are not playable rendered films.

## Backend

```powershell
.\.venv\Scripts\python.exe -m uvicorn movie_agent.api.app:create_app --factory --host 127.0.0.1 --port 8080 --workers 1
```

Port 8080 avoids the existing LLM tunnel on port 8000. Runtime workspace defaults to `workspace/studio` and can be set with `MOVIE_AGENT_WORKSPACE`. This local implementation requires one worker because tasks, resource locks and command arbitration are process-local. It is not a multi-process queue or public deployment.

REST commands/snapshots:

| Method | Path | Behavior |
|---|---|---|
| GET | /health | Application liveness; media mode |
| GET | /providers | Reasoning capability and live endpoint health |
| POST | /projects | Validate brief, assign ID, persist initial graph |
| GET | /projects/{id} | Project, application status, failure code, event cursor |
| POST | /projects/{id}/start | Start a created project; 202 |
| POST | /projects/{id}/pause | Pause after current node; 202 with policy |
| POST | /projects/{id}/resume | Continue a paused/failed project once reviews permit |
| GET | /projects/{id}/workflow | Graph snapshot and event cursor |
| GET | /projects/{id}/shots/{shot_id} | Existing typed Shot |
| GET | /projects/{id}/events | SSE history and live changes |
| GET | /projects/{id}/reviews | Approval records |
| POST | /reviews/{review_id}/resolve | Explicit approved Boolean and optional notes |
| GET | /jobs/{job_id} | Job state and provenance |
| POST | /jobs/{job_id}/cancel | Cooperative cancellation; prevents role commit |
| GET | /artifacts/{artifact_id} | Selected/latest artifact or requested version |

Unknown IDs return 404, invalid request bodies 422, conflicting lifecycle commands 409. Job/artifact IDs from Phase 1 may repeat across projects; supply `?project_id=...` to disambiguate. Ambiguity returns 409. Artifact selection follows the existing store semantics and `?version=N` addresses immutable versions.

The API defaults to manual human gates. Resolving an approval persists it but does not automatically start inference; issue resume afterward. A rejection remains a gate and cannot be bypassed with resume. Revision planning after rejection is intentionally outside the current command surface.

## SSE

Each SSE record contains `id: event_id`, `event: event_type`, and `data: EventEnvelope JSON`. Send `Last-Event-ID` or `?after=...` to replay events strictly after a cursor. Unknown cursors return 409 and require a snapshot refresh. `?follow=false` drains finite history; default follow=true stays connected with keep-alive comments. Slow/disconnected clients do not block production because they read the durable event journal projection.

Fetch a snapshot with its cursor, then subscribe after that cursor to reconstruct the workflow view. Events are atomically published as ordered files and survive restart. In-progress job transitions are visible through their lifecycle events; checkpoints capture complete execution state at role/node boundaries.

## Recovery and validation boundaries

Application metadata uses a ProjectRepository protocol with LocalProjectRepository as the default adapter. Transport imports only the application service; local adapters are assembled in the app factory. Artifacts and checkpoints continue to use Phase 1 stores. Database, Redis, Celery, GPU scheduling, frontend and media-model integration are excluded.

On shutdown, managed tasks are cancelled and current state is checkpointed. A new service instance restores state lazily; previously running records become paused, never silently restart external inference. A committed role is never resubmitted. An interrupted uncommitted synchronous request may be rerun because its remote completion cannot be queried after process loss.

The output validator checks structural/domain relationships and explicit commitments. It reports unverified free-form user constraints for human review; it cannot prove all arbitrary prose is semantically consistent. See `role_runtime.md` for the exact scope.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
$env:MOVIE_AGENT_RUN_INTEGRATION = "1"
.\.venv\Scripts\python.exe -m pytest tests/test_p2a_integration.py -q
```

Unit/API tests cover transport translation and errors, role context filtering, strict Schema export, Pydantic and semantic validation, bounded repair, all six incremental role slices, state commits, events, failure/restart inference reuse, human gates, pause/cancellation, and SSE replay. The integration probe validates the configured endpoint/model and a small constrained response. Real production is separately demonstrated by `run_p2a.py`.
