# Public Contracts

## Contract policy

All public schemas derive from `ContractModel` and use Pydantic v2. They are strict (`extra="forbid"`), versioned, JSON serializable, and JSON-Schema exportable. Open-ended metadata is limited to deliberate extension envelopes such as provider parameters, artifact metadata, and event payloads; core cinematic meaning remains typed.

```python
from movie_agent.domain import ProjectBrief

schema = ProjectBrief.model_json_schema()
payload = brief.model_dump_json()
restored = ProjectBrief.model_validate_json(payload)
```

## Contract groups

| Module | Contracts |
|---|---|
| `domain.project` | Project, ProjectBrief, CreativeDirection, StoryBible, VisualBible, canonical state, creative memory, generation history |
| `domain.cinematic` | Character/State, Location/State, Prop/State, Scene, Shot and camera/performance/lighting/frame types, ContinuityChain/State, GenerationStrategy/Request, PromptPackage |
| `domain.execution` | Artifact/Version, GenerationJob, CheckpointSnapshot |
| `domain.quality` | Evaluation/Issue, RepairAction/Plan |
| `domain.workflow` | WorkflowNode/Edge/Graph/Event, EventEnvelope, HumanReviewRequest |
| `domain.providers` | ProviderCapability/Request/Result/Selection, RoutingRequest |

Stable enums define every lifecycle, quality issue, repair action, provider failure, strategy, artifact kind, resource class, and workflow edge kind. Serialized enum values are lower-case protocol values rather than UI labels.

## Compatibility rules

- Consumers must inspect `schema_version` before applying migrations.
- Adding optional fields is backward-compatible within a major schema line.
- Renaming/removing fields or changing meaning requires a migration and schema-version change.
- Artifact IDs, job IDs, event IDs, review IDs, and graph IDs are opaque stable identities.
- A URI is an artifact location, never its identity.
- Provider-specific options belong in `GenerationRequest.parameters` or adapter configuration, not `Shot`.
- UI-specific position, color values, and CSS belong in frontend state; nodes expose only `display_order`, semantic `color_role`, and `icon_hint`.

## Validation boundaries

Validation occurs when briefs enter the system, graphs are constructed/restored, continuity transitions are derived, jobs transition, providers receive normalized requests, artifacts register versions, and checkpoints load. This prevents loosely typed dictionaries from becoming hidden cross-layer APIs.

## Event envelope

All lifecycle notifications use the same envelope fields: event ID/type/time, project and trace IDs, optional node/job IDs, versioned payload, and schema version. Payload content is deliberately JSON-only so it can cross future WebSocket, queue, or log transports without importing backend classes.

## Phase 2A additive contracts and migration reasoning

Repository inspection found no typed screenplay or role plan wrapper. `domain.planning` adds `Dialogue`, `ScreenplayScene`, `Screenplay`, `PlanningCommitments`, `ScenePlan` and `ShotPlan`. Screenplay declares canonical entities; ScenePlan/ShotPlan contain the existing Scene/Shot/ContinuityChain contracts. There is no parallel model-specific cinematic schema.

`Project.screenplay` is the only added persisted Project field and defaults to null. Old Phase 1 snapshots therefore validate unchanged; tests continue exercising the original schema round-trip. `STRUCTURED_TEXT` extends the strategy enum so the existing GenerationRequest can represent reasoning without pretending it generates video. Existing enum values and serialized field names are unchanged.

New EventType values describe provider request start/completion, role output receipt/validation success/failure and checkpoint creation. EventEnvelope itself is unchanged. Runtime state (`RoleResult`, attempts, validation reports, role selection and explicit revision history) is stored in the existing checkpoint `project_state` extension envelope. Missing P2A state defaults to empty when reading Phase 1 snapshots.

Schemas retain version 1.0.0 because the reader changes are additive and defaulted. A future incompatible change requires explicit versioned migration. Serving strictness is an adapter concern: defaulted fields are required in the generated serving Schema but remain backward-compatible in persisted Pydantic contracts.

The Cinematographer has a deliberately separate request-only contract. `CanonicalChainContext` is Core-owned input, while `ShotPlanDraft`/`ShotLocalStateDraft` contain only LLM-owned cinematic choices and scene-local deltas. The output Schema cannot express a continuity chain, canonical snapshot, shot identity, or previous/next topology. `CinematographerDraftMapper` validates local IDs and deterministically maps the draft into the existing persisted `ShotPlan`, `Shot`, `ContinuityState`, and `ContinuityChain` contracts. This changes ownership at the provider boundary without creating a second persisted Cinematic IR.
