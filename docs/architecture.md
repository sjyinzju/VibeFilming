# Movie Agent Core Architecture

## Decision summary

Movie Agent Core is a Python 3.12 and Pydantic v2 domain framework. It deliberately contains no model SDK, HTTP server, frontend, GPU runtime, or deployment assumption. The central design is one Showrunner coordinating role-labelled workflow nodes while deterministic code owns graph validation, continuity, scheduling, retries, artifacts, checkpoints, and events.

The domain does not depend on an orchestration engine. `WorkflowGraph` is the durable contract; `ProductionGraph` is the current local adapter. A future LangGraph, queue, database, or remote provider adapter must translate at the boundary instead of replacing the domain types.

## 1. System Architecture

```mermaid
flowchart TB
    Input[ProjectBrief] --> Showrunner[Showrunner Orchestrator]
    Showrunner --> Roles[Role / Skill / Node Layer]
    Showrunner --> DAG[WorkflowGraph]
    Roles --> IR[Cinematic IR]
    IR --> Continuity[Continuity + Frame Planning]
    IR --> Prompt[Prompt Compiler Boundary]
    Prompt --> Router[Model Router]
    Router --> Providers[Provider Interfaces]
    Showrunner --> Jobs[Jobs + Local Scheduler]
    Jobs --> Providers
    Providers --> Registry[Artifact Store + Provenance]
    Registry --> Critics[Technical QC + Critics]
    Critics --> Repair[Finite Repair Planner]
    Repair --> Jobs
    DAG --> Checkpoints[Checkpoint Store]
    Jobs --> Events[Event Bus]
    Registry --> Events
    DAG --> Events
```

Dependencies point inward: domain contracts have no dependency on orchestration, execution, storage, providers, or UI. Cinematic algorithms depend only on domain contracts. Local infrastructure implements replaceable interfaces.

## 2. Agent Role Architecture

```mermaid
flowchart TB
    S[Showrunner] --> CP[Creative Producer]
    S --> SA[Story Architect]
    S --> SW[Screenwriter]
    S --> D[Director]
    D --> C[Cinematographer]
    D --> V[Visual Director]
    S --> CS[Continuity Supervisor]
    S --> SP[Sound/Post Director]
    S --> CR[Critic]
    CR --> RP[Repair Planner]
    RP --> S
    G[Deterministic Graph / Jobs / Scheduler] --- S
```

Roles are `RoleSpec` descriptors and workflow responsibilities, not independently deployed conversational agents. A role can later be implemented by a skill, local function, subgraph, or provider call. Provider choice remains outside the role contract.

## 3. Production DAG

```mermaid
flowchart LR
    B[Brief] --> CE[Creative Expansion] --> ST[Story Planning]
    ST --> HG1{{Story Gate}} --> SC[Treatment / Screenplay]
    SC --> BV[Story + Visual Bibles] --> SE[Scene Planning]
    SE --> SH[Shot Planning] --> HG2{{Shot Gate}}
    HG2 --> AP[Asset Planning] --> SB[Storyboard Planning]
    SB --> PR[Shot Production] --> TQ[Technical QC]
    TQ --> VS[Visual/Semantic Critic] --> CC[Cinematic Critic]
    CC --> RA[Repair / Accept] --> AU[Audio / Post]
    AU --> RC[Rough Cut] --> FR[Full Film Review]
    FR --> HG3{{Final Gate}} --> FN[Final Render]
```

The standard flow is data, not an executor-owned array: nodes and typed edges are validated for unique IDs, valid references, and acyclicity. Edge vocabulary includes dependency, condition, success, failure, repair, and human gate.

## 4. Critic / Repair Loop

```mermaid
flowchart LR
    A[Artifact Version N] --> T[Technical QC]
    T --> V[Visual/Semantic Critic]
    V --> C[Cinematic Critic]
    C --> E{All evaluations pass?}
    E -- Yes --> S[Select immutable version]
    E -- No --> I[Issue Classifier]
    I --> P[Repair Policy + Plan]
    P --> R{Retry budget available?}
    R -- Yes --> J[New generation job]
    J --> A2[Artifact Version N+1]
    A2 --> T
    R -- No, generation failure --> H[Human Review]
    R -- No, shot-design failure --> D[Director Replan]
```

Evaluation is structured evidence, not a Boolean. Repair actions route from issue types and always respect the shot retry budget. Old artifact versions remain immutable.

## 5. Continuity Chain

```mermaid
flowchart LR
    S0[Chain Initial State] --> V1[validate_transition]
    V1 --> Q1[Shot N]
    Q1 --> E1[apply_shot_effects]
    E1 --> L1[Shot N Last Frame Artifact]
    L1 --> F2[Shot N+1 First Frame Anchor]
    E1 --> S1[Derived Boundary State]
    S1 --> V2[validate_transition]
    F2 --> V2
    V2 --> Q2[Shot N+1]
```

Character, prop, location, and global scene state are checked before a provider request exists. The scheduler also serializes jobs sharing a continuity chain; unrelated chains can use available capacity concurrently.

## 6. Provider Abstraction

```mermaid
flowchart LR
    IR[Shot + Strategy] --> PC[PromptCompiler]
    PC --> GR[GenerationRequest]
    GR --> MR[ModelRouter]
    MR --> CAP[Capability Match]
    CAP --> LLM[LLMProvider]
    CAP --> VIS[VisionProvider]
    CAP --> IMG[ImageProvider]
    CAP --> VID[VideoProvider]
    CAP --> AUD[AudioProvider]
    LLM & VIS & IMG & VID & AUD --> RES[Normalized ProviderResult]
    RES --> ART[Artifact Registry]
```

Routing considers task, quality profile, required capabilities, strategy, and logical resource class. Provider adapters normalize failure type and retryability. No provider name or endpoint appears in Cinematic IR.

## 7. Event to Future Workflow Canvas

```mermaid
flowchart LR
    G[Graph Mutations] --> EB[EventBus]
    J[Job Lifecycle] --> EB
    A[Artifacts] --> EB
    Q[Evaluation / Repair] --> EB
    H[Human Gates] --> EB
    EB --> ENV[Versioned EventEnvelope]
    ENV --> WS[Future transport adapter]
    WS --> CANVAS[Workflow Canvas]
    ENV --> LOG[Audit / telemetry consumer]
    ENV --> NOTIFY[Notification consumer]
```

The local event bus provides an ordered in-memory stream. A future frontend should subscribe through a transport adapter and project node, edge, job, artifact, evaluation, repair, and review events into its own view state. UI coordinates and CSS do not belong in the backend node contract.

## Deterministic execution

`GenerationJob` owns lifecycle, stable identity, dependencies, priority, resource class, bounded retry, cancellation, timestamps, and resulting artifact IDs. `LocalJobScheduler` waits for DAG dependencies, uses weighted logical capacity, and locks each continuity chain. `LocalJobExecutor` retries only normalized retryable failures and stops at the persisted budget.

The local implementation is intentionally small but its boundaries permit a durable queue later. A replacement must preserve job transition semantics, idempotency, event emission, cancellation, and retry counts.

## Durability and explainability

`LocalArtifactStore` uses stable artifact IDs and append-only versions. Files are stored at versioned URIs and never overwritten. Selection changes registry metadata, not content. Provenance records the responsible role, tool, provider, prompt package, inputs, strategy, parameters, evaluations, and repair plans.

After each completed workflow node, `LocalCheckpointStore` atomically saves graph state, project state, completed nodes, jobs, artifact records, evaluations, repair plans, and retry counters. Resume requeues interrupted active jobs, preserves terminal records, restores unresolved human reviews, and skips completed nodes.

## State separation

- Canonical Project State contains confirmed production facts and completed workflow state.
- Creative Memory contains preferences, rejected choices, and role notes.
- Generation History records strategy/provider outcomes and issue codes.

No vector database is required for these contracts. Retrieval or embedding can be added later as an adapter over stable IDs.

## Deferred by design

This phase does not include Spark/DGX integration, model downloads, ModelScope, ComfyUI, vLLM, a formal server deployment, React, authentication, or real media generation. None is needed to execute and verify the mock film pipeline.
