# Movie Agent Core

Movie Agent Core is a model-agnostic, frontend-agnostic, and deployment-agnostic foundation for long-running AI film production. This phase contains typed cinematic contracts, deterministic orchestration and execution infrastructure, mock providers, and a fully local mock production flow. It does not connect to real models, Spark, ComfyUI, vLLM, or a web frontend.

See `docs/architecture.md` for the design and `tests/fixtures/sample_brief.json` for the mock workflow input.

## Run locally

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
- `docs`: architecture, IR, workflow, integration contracts, reference audit
