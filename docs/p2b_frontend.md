# Phase 2B — Web Studio

Web Studio is a local, desktop-first controller for the frozen P2A production runtime. The normal server uses all six real reasoning roles and Mock media. The offline E2E server uses a clearly labelled fake reasoning provider with the same FastAPI, Core, validation, checkpoint, human gate and durable event paths.

## Start

From the repository root, with the existing Python environment and private `.env` configured:

```powershell
.\.venv312\Scripts\python.exe -m uvicorn movie_agent.api.app:create_app --factory --host 127.0.0.1 --port 8080 --workers 1
```

In another terminal:

```powershell
cd E:\movie-agent\web
npm ci
npm run dev
```

Open <http://127.0.0.1:5173>. The default `VITE_API_BASE_URL=/api` goes through Vite's same-origin proxy to FastAPI at `127.0.0.1:8080`. Copy `web/.env.example` only if you need to override the base path. `MOVIE_AGENT_API_PROXY` configures the development proxy target. The browser never receives model credentials or talks to a model endpoint.

`npm run build` produces `web/dist`. A production static host must proxy `/api` to FastAPI with SSE buffering disabled. `vite preview` previews the static assets; it is not a production backend/proxy deployment. Authentication, multi-tenancy and public deployment are outside this local phase. Run the backend with one worker, as required by P2A's local task/event coordination.

## Frontend architecture

- React 19 / TypeScript / Vite, React Flow (`@xyflow/react`) and Dagre.
- TanStack Query owns server snapshots, provider reads and command reconciliation. React state owns panel selection and unsubmitted forms; a separate global state library is unnecessary.
- `web/src/api/client.ts` centralizes every REST command/read and `VITE_API_BASE_URL`.
- `web/src/api/events.ts` handles fetch-based SSE framing, UTF-8 chunks, Last-Event-ID and cancellation.
- `web/src/state/useStudio.ts` coordinates snapshot, replay, live events, reconnection and bounded refresh.
- `web/src/state/workflow.ts` projects statuses, bounded event history and progress.
- `web/src/state/canvas.ts` derives frontend-only nodes and edges and computes stable positions.
- Components separate Brief, Canvas and Inspector. Node renderers have stable identities and are memoized. Existing positions bypass Dagre on status-only updates.

### Layout and visual system

The interface defaults to Simplified Chinese. The toolbar's 中文 / English selector switches UI labels immediately and saves the preference locally for refresh/reopen. Interface language is independent of `ProjectBrief.output_language`: switching it does not alter story text, review notes, model outputs, raw JSON, API enum values or canonical production state.

Localization verification (2026-09-06): 18 frontend tests and 4 Mock browser E2E scenarios passed, including Chinese default, English persistence after refresh, unchanged story/output language, and preserved review notes during live switching. `MOVIE_AGENT_STUDIO_TEST_PORT=5174` runs browser tests alongside an existing Studio on 5173.

The toolbar provides project switching and state-controlled Start/Pause/Resume/Cancel. Below it, workflow completion reports the active node and completed/known executable nodes. The main area is a left creative console, a dominant Canvas and a collapsible Inspector. At narrower desktop widths the Inspector becomes an overlay; below 900px the Brief also becomes a drawer.

`web/src/styles.css` defines warm background/surface/paper, text/muted, border, burnt-orange accent, error, amber review, success and grid tokens. Serif editorial headings contrast with compact sans-serif controls. Status always includes text or an icon, not color alone. Controls have focus outlines, labels, accessible names and reduced-motion support. The initial Canvas uses readable zoom; Fit View offers an overview of the entire long DAG, and Focus Current Stage returns to a readable working view.

### Creative Brief

Only `story_description` is required. Application defaults supply title, logline and 30 seconds; original P2A request bodies remain supported. Quick mode shows title, story, duration and visual style. Advanced sections expose all 47 editable ProjectBrief fields (excluding contract metadata) from the generated backend schema:

- Basic: title, logline, story, duration, language, genre, audience, rating.
- Story: themes, narrative style, ending, pacing, dialogue density, creative freedom.
- Characters: count, add/remove description cards, relationship constraints.
- World: locations, time period, science-fiction switch, world rules, prohibited elements.
- Visual: style, movies/images, palette, lighting, realism, aspect ratio, resolution, fps.
- Cinematography: shot styles, motions, focal lengths, cutting style, composition.
- Audio: dialogue, voice, music, ambience and sound design.
- Production: quality, shot limit, repair budget and generation budget.
- Constraints: user constraints and must-preserve commitments.

Controls use labelled inputs, textareas, add/remove cards, tags, suggested chips, numeric inputs, sliders, selects and enum-derived segmented choices. Field defaults, types and numeric limits come from generated `brief.schema.json`, not a separate DTO.

Story-first, scene-guided and key-moment modes can be mixed. `CreativeHints` contains optional user-authored `SceneSeed`, `KeyMoment`, `KeyVisualHint`, and `StyleReference` values. Reference works distinguish film, series, director, photography and visual references, with optional visual/lighting/cinematography/color/editing-rhythm/narrative-tone dimensions. These are inspiration metadata, not instructions to copy a work.

## Backend boundary and compatibility

P2A freeze baseline: `53f4114`. No changes to Shot, ContinuityState, ContinuityChain, the frozen ContextBuilder, RoleRunner, validation, provider semantics, Spark or serving configuration.

Minimal additions:

| Boundary | Purpose |
| --- | --- |
| `CreateProjectInput(ProjectBrief)` | Existing flat POST bodies plus application defaults and optional `creative_hints` |
| `ProjectRecord.creative_hints` | Persist hints separately from canonical Project/IR; defaults for old records |
| `CreativeInputContextBuilder` | Inject labelled user input into relevant role context; empty hints preserve the exact legacy payload/hash/version |
| `GET /projects` | Discover saved projects, including after a page reload |
| `GET /projects/{id}/studio` | Atomic typed project + graph + cursor + jobs + artifacts + reviews + role results + evaluations/repair read model |
| `POST /projects/{id}/cancel` | Stop local execution, finish cancelled job/node records and retain checkpoint/history; terminal application status |

Scene seeds are not canonical scene IDs or state. Scene-scoped Cinematographer context omits proposed scene seeds while retaining user-authored moments and visual inspiration. Hints never replace chain context or enter the frozen brief's constraint strings. Repository persistence restores the hints-aware application context builder after restart. Checkpoints and role context/provenance retain normal P2A behavior.

Cancel stops the local production task and prevents later local commits. It does not promise remote inference termination or reset any budget. Pause retains P2A's after-current-node behavior.

### Generated types

```powershell
cd E:\movie-agent\web
npm run gen:api
```

The generator imports the actual FastAPI application with a non-executing service object; no backend process, `.env` or provider connection is needed. It exports OpenAPI and ProjectBrief JSON Schema, then runs `openapi-typescript`. Generated files are checked into the source tree and build offline once dependencies are installed. `MOVIE_AGENT_PYTHON` can select another Python executable; Windows defaults to `.venv312`.

Input types retain optional defaulted fields. The small recursive `Serialized<T>` output adapter derives required response fields from generated types because FastAPI response serialization emits defaults. It does not invent fields or enums. Backend Pydantic/OpenAPI remain authoritative.

## Snapshot, SSE and Canvas

1. Load the atomic Studio snapshot and its event cursor.
2. Subscribe to `/projects/{id}/events` with Last-Event-ID.
3. Apply known status/progress events immediately; deduplicate by event ID.
4. Coalesce event-triggered typed snapshot reads at 180ms to complete partial event payloads, new entities, jobs, artifacts, validation and review data.
5. Reapply events received while a snapshot read is in flight. Reconcile every five seconds because command status can settle just after a node event.
6. On transport failure, reconnect from the last received cursor with capped backoff. HTTP 409 invalidates the cursor and reloads a snapshot. Browser offline events actively abort an established stream. Recovered-event counts stop at the captured replay boundary.

The UI displays Live/Reconnecting/Offline and surfaces failed API requests inline. Trace displays the latest 200 durable event envelopes, including validation, repair, checkpoint and provider outcome events; it does not capture chain-of-thought.

The **actual P2A graph creates all 21 executable stage nodes at project creation**. Studio shows this real planned graph instead of pretending that the backend creates roles incrementally. Later NODE_CREATED/EDGE_CREATED events hydrate from the typed snapshot. Committed Scene and Shot objects add clearly labelled, non-executable planning nodes and projection edges; they are excluded from executable progress counts. No demo graph drives the application.

Role/Scene/Shot/Human/Production/Final nodes share one restrained design. Backend edge types distinguish dependency, repair, failure and human-gate routes. Edges animate while their target runs. Dagre lays out the first graph and newly arriving nodes; known positions remain fixed. Dragging, pan, zoom, Fit View, Reset View, Focus Current Stage and explicit Auto Layout are available. Positions and viewport are stored under a versioned, project-specific localStorage key and never sent to the backend.

### Progress

The projection assigns equal weight to five semantic stages: development, cinematography, production, review/repair, finishing. Node status/progress supplies each stage's contribution. A local high-water mark avoids regressions when the graph gains nodes. Non-completed workflows are capped at 99%; only authoritative completion reaches 100%. Completed/known node counts stay literal. The projection is not an ETA and never claims time remaining.

## Inspector, review and artifacts

Node Summary/Raw JSON exposes actual role context, target contract, committed output, validation attempts/replays, dependencies, timestamps, jobs, errors and artifacts. Scene/Shot inspection uses the actual canonical objects. Critic and repair stages expose structured evaluations and repair plans.

On a pending review, Inspector opens the relevant node. Approve uses the existing resolve API, then Resume. Request Revision submits `approved=false` with notes and preserves the resolved rejection. **P2A has no arbitrary replan/revision API: rejection holds the production, and does not silently rerun roles or reset budgets.** The UI explains this boundary. A user can retain that audit and start a new brief; this phase does not alter rejected historical results.

Models distinguishes the configured/served reasoning provider from six explicit Mock media groups. Fake reasoning is labelled as deterministic test reasoning. Artifact cards show identity, type, immutable version, selection and provenance. Mock artifacts are labelled; placeholder movie files are never presented as playable films.

Drafts are versioned local autosaves. Submitted projects are backend-owned and read-only in the Brief panel. URL project identity plus server snapshots restore the workspace; localStorage is not a canonical production database. Storage failures do not prevent an in-session production.

## Verification

Local acceptance on 2026-09-05: P2A baseline **87 passed, 1 opt-in skipped**; after application extensions, full backend suite **90 passed, 1 opt-in skipped**. Frontend **16 tests passed**; TypeScript and production build passed. Mock browser acceptance **3 tests passed**, including real browser offline/replay and persisted dragged coordinates. The real-brain browser test is provided and its configuration loads; no new real inference was run for this P2B acceptance. P2A's frozen production/history remains untouched.

```powershell
# From repository root
.\.venv312\Scripts\python.exe -m pytest -q
# From web/
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

The Mock browser suite launches its own API at 8081 and Vite at 5173, uses an isolated `workspace/p2b-e2e`, and exercises real API/Core/SSE behavior. It covers story-only creation, draft autosave, growing Scene/Shot projections, three real human-gate commands, Mock completion, refresh recovery, layout persistence, offline event replay, Pause/Resume/Cancel and absence of browser model calls. Stop any existing development server on 5173 before running it.

Backend tests cover minimal/legacy request compatibility, hint provenance and restart, unchanged empty-hint role contexts, complete Inspector snapshots, rejected-review hold and cancellation without commit. Frontend tests cover form validation, advanced fields/cards, project requests, API failures, snapshot/status mapping, chunked SSE, invalid cursor recovery, progress, node coordinates, Inspector, review actions and Mock badges.

### Explicit real-brain browser integration

Regular CI never contacts Spark. To intentionally start one new production with the already-configured real backend:

```powershell
# Root terminal: keep this separate from the frozen P2A acceptance workspace.
$env:MOVIE_AGENT_WORKSPACE = 'workspace/p2b-real'
.\.venv312\Scripts\python.exe -m uvicorn movie_agent.api.app:create_app --factory --host 127.0.0.1 --port 8080 --workers 1
# Another terminal, web/
npm run dev
# Another terminal, web/
$env:MOVIE_AGENT_RUN_P2B_REAL = '1'
npm run test:e2e:real
```

`MOVIE_AGENT_STUDIO_URL` can select an already-running Studio. Without the explicit environment flag the real test skips. It creates one new project, approves three review gates, and requires completed production with real Shots and Mock media. It does not revise or reuse the frozen P2A acceptance project. Ordinary real runtime validation/repair budgets remain in force; failure is reported, not hidden by fallback.

## P3 extension points and present limits

P3 can add real providers behind the existing provider boundary, expose safe artifact preview URLs, and enrich provider capabilities. Artifact cards and typed scene/shot inspection already supply identity, version, selection and provenance for previews. No image/video/VLM/audio model, GPU scheduling, ComfyUI, LoRA, download or real final rendering is included here. P2B ends at the Studio boundary; P3 has not started.
