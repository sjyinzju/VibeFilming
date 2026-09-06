# Movie Agent Core

Movie Agent Core is a model-agnostic foundation for long-running AI film production. Phase 2A adds six real reasoning roles through a configurable OpenAI-compatible endpoint and a FastAPI/SSE backend. Phase 2B adds a React Web Studio. Phase 3 adds the provider-neutral media runtime, binary artifact transport, preview/QC/repair, audio timeline, post-production contracts, and model-service lifecycle boundary.

**Phase 3 Media Runtime Foundation is ready. Real media models are not installed.** Bundled Image, Video, Vision, Speech/Music/SFX/Foley, and Post providers remain deterministic Mock adapters that create small valid PNG/MP4/WAV test assets through the real runtime path.

See `docs/architecture.md` for the design and `tests/fixtures/sample_brief.json` for the mock workflow input.

## Phase 2A

**Phase 2A is complete and frozen at `53f4114`.** The accepted `workspace/p2a-real` run used the real OpenAI-compatible reasoning provider for all five upstream roles and all three scene-level Cinematographer plans. It produced three validated real Shots (18 seconds total), then completed the existing mock frame/image/video/critic/audio/post/final pipeline and emitted `WORKFLOW_COMPLETED`. P2B builds on that frozen behavior. Serving changes and real media providers remain out of scope.

Configure `.env` from `.env.example` and keep the existing serving tunnel available. Use a Python 3.12+ virtual environment. Full runtime/API documentation is in [docs/p2a_runtime.md](docs/p2a_runtime.md); role and provider contracts are in [docs/role_runtime.md](docs/role_runtime.md) and [docs/llm_provider.md](docs/llm_provider.md).

The final acceptance evidence, request-contract ownership model, real invocation metrics, hash checks, and freeze checkpoint are recorded in [docs/p2a_verification.md](docs/p2a_verification.md).

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe scripts/run_p2a.py --workspace workspace/p2a-demo
.\.venv\Scripts\python.exe -m uvicorn movie_agent.api.app:create_app --factory --host 127.0.0.1 --port 8080 --workers 1
```

The backend exposes project start/pause/resume, workflow/shot/job/artifact snapshots, human review resolution and replayable SSE events. API human gates require explicit approval. The CLI auto-approves demonstration gates. Media remains placeholder output.

To stop before Director and verify inference reuse:

```powershell
.\.venv\Scripts\python.exe scripts/run_p2a.py --workspace workspace/p2a-resume --stop-after-node bibles
.\.venv\Scripts\python.exe scripts/run_p2a.py --workspace workspace/p2a-resume --resume
```

Regular tests never require the LLM endpoint. Opt in with `MOVIE_AGENT_RUN_INTEGRATION=1` when running `tests/test_p2a_integration.py`.

## Phase 3 media foundation

See [P3 runtime](docs/p3_media_runtime.md), [media contracts](docs/media_contracts.md), [providers](docs/media_providers.md), [artifacts](docs/media_artifacts.md), [model services](docs/model_services.md), and the [extension guide](docs/p3_extension_guide.md).

No P3 command downloads or launches a model. A real image adapter begins at `ImageProvider.generate(ImageGenerationRequest)`; a real video adapter begins at `VideoProvider.generate(VideoGenerationRequest)`.

## Run locally

### Web Studio (Phase 2B)

Start the existing configured backend from the repository root:

```powershell
.\.venv312\Scripts\python.exe -m uvicorn movie_agent.api.app:create_app --factory --host 127.0.0.1 --port 8080 --workers 1
```

Then start the frontend in a second terminal:

```powershell
cd E:\movie-agent\web
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. Only your story is required; Advanced exposes the full brief and optional typed creative guidance. Approve human gates in Inspector to continue. Reasoning is real on the normal backend; media remains clearly labelled Mock. The frontend's `/api` proxy calls FastAPI only.

See [Web Studio architecture, controls, tests and real opt-in E2E](docs/p2b_frontend.md). API types regenerate with `npm run gen:api` and are included in source; building does not require a running backend. Frontend checks: `npm test`, `npm run build`, `npm run test:e2e`.

### Core CLI

Python 3.12 and Pydantic v2 are the supported runtime.

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python scripts/run_mock.py tests/fixtures/sample_brief.json --workspace workspace/demo
```

The mock run performs creative/story/screenplay/scene/shot planning, creates boundary-frame artifacts and video jobs, deliberately fails one first-pass shot, repairs it as an immutable second version, assembles audio/timeline/final placeholders, writes checkpoints, and emits the complete event stream.

To demonstrate restart and resume:

```powershell
python scripts/run_mock.py tests/fixtures/sample_brief.json --workspace workspace/resume-demo --stop-after-node storyboard_planning
python scripts/run_mock.py tests/fixtures/sample_brief.json --workspace workspace/resume-demo --resume
```

## Package map

- `movie_agent/domain`: versioned contracts and stable enums
- `movie_agent/cinematic`: continuity, frames, strategies, prompt boundary
- `movie_agent/orchestration`: Showrunner graph, roles, human gates
- `movie_agent/execution`: jobs, logical scheduler, events, checkpoints
- `movie_agent/providers`: provider ABCs, router, deterministic mock
- `movie_agent/artifacts`: immutable local artifact versions
- `movie_agent/quality`: technical QC, critics, finite repair policy
- `movie_agent/services`: complete model-free production composition
- `movie_agent/media`: typed media contracts, strategy, compilers, storage, preview and runtime
- `movie_agent/model_services`: future service lifecycle boundary and basic manager
- `docs`: architecture, IR, workflow, integration contracts, reference audit
