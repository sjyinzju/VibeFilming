# Reference Audit

## Scope and method

This audit was completed before implementation. The `reference/` tree was treated as read-only. For every referenced project, the root README, root license (when present), top-level/core directory structure, and the implementation paths related to orchestration, state, shot planning, continuity, providers, generation, retry, jobs, checkpoints, and artifacts were inspected.

No source code from the reference projects is copied into `movie-agent`. The implementation uses independently designed contracts and standard-library/Pydantic code. This keeps attribution simple, avoids importing model-specific assumptions, and is mandatory for MovieAgent because its repository snapshot has no root license.

## Findings by project

### ViMax (`reference/vimax`)

- **License:** MIT.
- **Core architecture:** Pydantic interfaces in `interfaces/`; specialized planning/generation roles in `agents/`; idea/script/novel pipelines in `pipelines/`; generator protocols in `tools/protocols.py`; an interactive agent/tool loop with session indexing and resumable on-disk workspaces in `agent_runtime/`.
- **Relevant implementation:** `Script2VideoPipeline` persists intermediate JSON and media by stage, uses `asyncio.Event` to express frame/shot readiness, groups shots into camera dependency trees, generates character references, produces first/last frames, and waits for frame prerequisites before video generation. The runtime has tool schemas, session records, transitions, retry helpers, and provider adapters.
- **Worth reusing as design ideas:** explicit first/last-frame planning; separating narrative planning from rendering; persisting stage products; using dependency signals for parallel work; role-shaped components rather than requiring one model service per role.
- **Not suitable here:** contracts contain prompt text and generator-specific fields; pipelines call concrete image/video implementations and filesystem paths directly; resume is mostly “file already exists”; orchestration state is not a general production DAG; event and artifact provenance contracts are incomplete for a long-running production system.
- **Direct code use:** no.
- **Concrete inspiration for movie-agent:** `FrameAnchors` as first-class IR, continuity-chain serialization, artifact-backed intermediate products, and deterministic scheduling of independent work.

### Flow (`reference/flow`)

- **License:** MIT.
- **Core architecture:** a headless `Pipeline` coordinates writer, keyframe/video generation, post-production, and publishing. `store/` contains Pydantic project/timeline/media aggregates backed by SQLModel. `ParallelGenerator` implements two-pass first/last-frame-to-video work. `agent/` contains a tool loop and background generation jobs.
- **Relevant implementation:** N scenes produce N+1 boundary keyframes; each scene consumes adjacent keyframes and can run independently. Media assets are separated from timeline clips, project revisions provide optimistic mutation tracking, and generated media has lifecycle status and provenance fields. Clip validation has bounded retries.
- **Worth reusing as design ideas:** boundary-frame planning; stable media IDs separate from paths; project aggregate plus revisions; scene/chain parallelism; model/backend calls behind a generation service boundary.
- **Not suitable here:** the main pipeline is procedural rather than an explicit DAG; the scheduler is a topic loop, not a resource scheduler; jobs are daemon-thread helpers without a durable job contract; schemas are intentionally small and model/prompt fields leak into timeline objects; quality control and repair routing are shallow.
- **Direct code use:** no.
- **Concrete inspiration for movie-agent:** anchor-pair generation strategy, artifact registry/versioning, logical resource scheduling, and frame-aware production dependencies.

### PenShot / story-shot-agent (`reference/story-shot-agent`)

- **License:** MIT.
- **Core architecture:** LangGraph-based workflow orchestration in `neopen/agent/workflow/`; Pydantic workflow state split into input/domain/execution/error/config/output sections; task queue/lifecycle/repository in `neopen/task/`; continuity models and guardians in `neopen/agent/continuity_guardian/`; short/medium/long-term memory in `neopen/knowledge/memory/`.
- **Relevant implementation:** explicit nodes, edges, conditional repair/retry/human routes, stateful checkpoints, async task processing with priorities and concurrency limits, structured continuity issue/severity/state snapshots, cross-chunk validation, and bounded workflow decisions.
- **Worth reusing as design ideas:** distinguish canonical domain state from execution state; expose conditional repair and human-intervention paths; validate graphs; maintain structured continuity issues; keep orchestration adapters outside domain state.
- **Not suitable here:** domain state and pipeline routing remain closely shaped around LangGraph and the storyboard use case; continuity relies partly on prompts/vector retrieval instead of a deterministic transition contract; task persistence and workflow code carry framework/application concerns; it does not provide the full artifact/provider/scheduler/event model required here.
- **Direct code use:** no.
- **Concrete inspiration for movie-agent:** engine-independent graph contracts with an optional future LangGraph adapter, structured continuity diagnostics, checkpointable state, human gates, and bounded decision loops.

### MovieAgent (`reference/MovieAgent`)

- **License:** no root license file is present in the supplied snapshot. Nested model components have their own licenses, which do not license the repository as a whole.
- **Core architecture:** a hierarchical script → scene → shot planning flow in `movie_agent/run.py`; role-specific prompts for screenwriter, scene planner, and shot planner; a `ToolCalling` layer selects image/audio/talking/video model implementations; generated stage JSON is written to result files.
- **Relevant implementation:** separate creative roles reason at different narrative levels; scene outputs include characters, plot, emotion, visual style, props, sound, and cinematography; shot outputs include purpose, framing, camera motion, positions, and dialogue.
- **Worth reusing as design ideas:** hierarchical film-planning roles and the scene-to-shot decomposition vocabulary.
- **Not suitable here:** provider names and APIs are hard-coded in agents/tools; contracts are prompt-defined JSON rather than stable domain types; control flow is procedural; no general DAG, durable job/event/artifact/checkpoint system, or deterministic continuity engine; no root license permits source reuse.
- **Direct code use:** no, including no prompt reuse.
- **Concrete inspiration for movie-agent:** Showrunner coordination over role/skill nodes and cinematic vocabulary in the model-agnostic IR.

### DiffSynth-Studio (`reference/DiffSynth-Studio`)

- **License:** Apache-2.0.
- **Core architecture:** a broad diffusion inference/training engine with `core/` device, loading, quantization, offload, and data infrastructure; `models/` and `pipelines/` isolate model families; examples describe model capabilities and low-VRAM variants.
- **Worth reusing as design ideas:** capability-driven provider descriptions; keep device/offload concerns below the production domain; seed and parameter provenance; future resource-class routing.
- **Not suitable here:** it is a model runtime/training framework, not a movie-production orchestrator; importing it now would violate the no-model/no-Spark scope and couple the core to a concrete inference stack.
- **Direct code use:** no.
- **Concrete inspiration for movie-agent:** provider capabilities and opaque provider parameter/provenance envelopes, without importing any runtime.

### MoneyPrinterTurbo (`reference/MoneyPrinterTurbo`)

- **License:** MIT.
- **Core architecture:** service-oriented short-video assembly with controller, task, material, subtitle, voice, video, state, and task-artifact modules; local/Redis state managers; bounded thread pools for external work.
- **Worth reusing as design ideas:** structured stage failures, progress persistence, task-local artifact isolation, cancellation/busy-state checks, and provider-specific behavior behind service registries.
- **Not suitable here:** a linear application service optimized for stock-footage shorts; its state dictionaries and file conventions are not a stable cinematic IR or DAG; provider and UI concerns are mixed into production services.
- **Direct code use:** no.
- **Concrete inspiration for movie-agent:** normalized failure records, progress events, and explicit task/artifact ownership.

### ShortGPT (`reference/shortgpt`)

- **License:** MIT.
- **Core architecture:** content engines select a sequence of editing steps; an editing engine executes/render steps; audio, prompts, APIs, asset/database, and tracking are separated into packages.
- **Worth reusing as design ideas:** composable editing steps, delayed render assembly, cost/generation tracking.
- **Not suitable here:** flows are essentially ordered step lists, not dependency graphs; contracts are weak and tied to short-form content automation; resume and quality repair are not production-grade.
- **Direct code use:** no.
- **Concrete inspiration for movie-agent:** post-production as replaceable nodes and provenance/cost metadata.

### Open-Sora (`reference/Open-sora`)

- **License:** Apache-2.0.
- **Core architecture:** model/dataset registries, configuration-driven training/inference, distributed/parallel acceleration, activation and training checkpointing, and T2V/I2V pipelines.
- **Worth reusing as design ideas:** provider capability registration; reproducible seeds; resource/offload attributes belong in provider execution metadata, not Shot IR.
- **Not suitable here:** checkpointing is primarily tensor/training checkpointing rather than production-workflow recovery; the repository is an inference/training implementation tied to concrete models and GPU infrastructure.
- **Direct code use:** no.
- **Concrete inspiration for movie-agent:** a future adapter can translate generic provider requests into runtime configuration while the core remains unchanged.

### VideoLingo (`reference/VideoLingo`)

- **License:** Apache-2.0.
- **Core architecture:** numbered localization/dubbing stages, a task runner with pause/resume/stop, file-existence stage skipping, provider-specific ASR/TTS modules, and retry decorators.
- **Worth reusing as design ideas:** cooperative cancellation points; stage-level pause/resume; audio/post work as an independent subgraph; exponential retry only around retryable operations.
- **Not suitable here:** numbered modules form a fixed linear pipeline; “file exists” is used as checkpoint state; retries catch broad exceptions; artifacts are paths rather than versioned registry entries.
- **Direct code use:** no.
- **Concrete inspiration for movie-agent:** cooperative cancellation and explicit audio/post nodes, upgraded to typed jobs, artifacts, and checkpoints.

## Cross-project conclusions

The references consistently validate four ideas:

1. Long-form generation must be decomposed into scene/shot jobs with explicit frame and continuity dependencies.
2. First/last boundary frames unlock both continuity and safe parallelism.
3. Planning, provider execution, post-production, and persistence need separate interfaces.
4. Retry is only safe when bounded, classified, observable, and connected to repair or human escalation.

They also reveal recurring gaps this project must avoid: procedural step arrays, prompt-shaped dictionaries as contracts, concrete model names in domain objects, naked file paths as artifact identity, untyped status dictionaries, framework-owned domain state, and unbounded or overly broad retries.

## Architecture decision for movie-agent

- Build an independent Python 3.12/Pydantic v2 domain package.
- Make `Cinematic IR`, workflow graph, artifacts, jobs, events, providers, and checkpoints framework-neutral.
- Use one deterministic Showrunner executor over role-labelled nodes; roles are metadata/skills, not independently deployed model services.
- Use an explicit validated DAG with conditional edge kinds and human gates. A future LangGraph integration will be an adapter only.
- Use asyncio for the local executor and logical resource budgets. Serialize jobs sharing a continuity chain while allowing independent chains/scenes to run concurrently.
- Store immutable artifact versions and JSON checkpoints locally through replaceable interfaces.
- Compile IR to provider prompt packages only at the provider boundary.
- Implement deterministic technical QC and continuity validation before generation; route structured issues through a finite repair policy.

## License decision

Because no reference source is copied, this repository does not require source-level attribution notices for reused code. Reference project names and the architectural ideas learned from them are documented here for traceability. MovieAgent source and prompts are not reused because the supplied repository lacks a root license.
