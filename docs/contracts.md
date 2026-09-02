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
