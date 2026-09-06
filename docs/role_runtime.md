# Role Runtime

## Movie output language

UI language controls interface labels. `ProjectBrief.output_language` controls generated
creative content. New Studio drafts resolve an unset language from the UI at submission
(`zh-CN` or `en`); an explicit choice overrides it. After submission the two are independent.
Legacy API inputs still default to English. No existing project or committed output is migrated.

`LanguagePolicy` appends one model-independent instruction to `RoleDefinition.instruction`
at the shared `RoleRunner` prompt assembly point. All six reasoning roles and every structured
repair use that same system instruction. It requires the requested language for human-readable
story, screenplay, scene, shot, lighting, camera and frame descriptions, while preserving JSON
keys, IDs, enums, schema constants, technical literals, URLs and verbatim canonical/user values.
Empty legacy language values use the English policy without mutating the original brief.
Other explicitly supplied languages remain supported by the generic policy.

This is a generation instruction, not a claim of exhaustive deterministic language detection.
Existing Pydantic, semantic, continuity and verbatim-preservation checks remain unchanged.
The opt-in real-endpoint test checks specific CreativeDirection fields for Chinese content and
verifies that an English must-preserve constraint stays verbatim:

```powershell
$env:MOVIE_AGENT_RUN_LANGUAGE_INTEGRATION = '1'
.\.venv312\Scripts\python.exe -m pytest tests/test_language_policy.py -q
```

Verified with the configured real endpoint: one lightweight Creative Producer role, Chinese
premise/emotional arc/tone, valid structured output, and verbatim English constraint preservation.
The test uses an isolated in-memory Project and does not execute or rewrite existing projects.

## Execution

For measured role-specific timeouts, independent inactivity/total budgets, bounded
stream collection and explicit uncertain-completion recovery, see [long inference](long_inference.md).

For Core-owned terminal constraints, bounded shot-local semantic revision and the
audited real Scene 3 recovery, see [terminal semantic revision](terminal_semantic_revision.md).

The execution path is `WorkflowNode → RoleRunner → ContextBuilder → existing LLMProvider → structured decoding → Pydantic → semantic validation → state commit → checkpoint`. Only the Showrunner changes Project, node status, jobs, and scheduling. All six roles use this path.

| Role | Context projection | Output |
|---|---|---|
| Creative Producer | Brief story fields, hard constraints, creative freedom | `CreativeDirection` |
| Story Architect | Story brief, CreativeDirection | `StoryBible` |
| Screenwriter | StoryBible, CreativeDirection, relevant character declarations and writing constraints | `Screenplay` |
| Visual Director | Visual/camera brief, StoryBible, characters, locations | `VisualBible` |
| Director | Screenplay, StoryBible, VisualBible, entities and production constraints | `ScenePlan` containing existing `Scene[]` |
| Cinematographer | Current Scene/script intent, bibles, scene-local entities, input-only `CanonicalChainContext`, camera constraints, scene duration/shot budget | request-only `ShotPlanDraft`; Core maps it to existing `ShotPlan` / `Shot[]` / `ContinuityChain[]` |

ContextBuilder uses explicit allowlists; no jobs, event history, artifact provenance, GPU configuration or API secrets are accessible through context policies. JSON is canonicalized for a SHA-256 context hash and source IDs are sorted. An invocation with changed context cannot silently reuse a saved unsuccessful attempt.

RoleDefinition stores the named target schema and an OutputPolicy. RoleInvocation identifies project, node and optional scene. RoleResult persists the validated output envelope, context source IDs/hash/version, provider/model, schema name/version, timestamps, request IDs, prompt hashes, token usage, latency and validation attempts. Concrete output is always parsed using the registered Pydantic class before commit; the JSON envelope is a storage boundary, not a second cinematic schema.

## Validation and repair

1. The actual Pydantic target generates the serving JSON Schema. The adapter recursively makes defaulted object properties required for strict generation. This prevents semantic fields such as scene character membership from being omitted and silently defaulted to empty. It does not modify the persisted domain schemas.
2. Final `content` is parsed with `TargetModel.model_validate_json()` regardless of constrained decoding.
3. Deterministic checks verify entity catalogs, dialogue speakers, scene membership, screenplay-to-scene coverage, unique and safe IDs, shot/chain coverage, previous/next adjacency, state references, continuity transitions and scene endpoints, shot counts, duration budgets, and retry/quality policy.
4. Hard constraints and immutable fact ledgers must be carried forward exactly. Prohibited literal elements in positive creative content are checked, with limited negation handling.

General natural-language contradictions cannot be fully proven by deterministic rules. `ValidationReport.unverified_constraints` preserves user prose for human review; exact ledger preservation is not proof that every visual/narrative interpretation complies. The API therefore defaults to explicit story, shot-plan and final-cut human gates. This limitation is exposed rather than silently claiming complete semantic verification.

The default repair budget is two: one initial call plus at most two corrections. Corrections include the original task/context, original final-content output, precise validation errors and the same target Schema. No fields are dropped or guessed. Failed attempts are checkpointed so restart does not reset the budget. Exhaustion marks `ROLE_OUTPUT_INVALID` and fails the node. Media `RepairPlanner` remains separate.

A diagnosed schema/instruction defect may be retried through the CLI's explicit `--revise-role` command. It accepts only an exhausted failed role, archives all failed attempts, assigns a distinct revision invocation and allows one revision per role. It cannot reopen successful roles, skip nodes, or repeatedly reset budgets.

## State commits and recovery

Story Architect commits StoryBible at `story_planning`; the later `bibles` node only creates VisualBible in the real path. Screenplay declares the entity catalogs used by later roles. Director commits existing Scene objects. Cinematographer runs per scene; local-draft validation and deterministic mapping produce existing Shot/ContinuityChain objects, then the normal semantic/continuity checks run and Scene.shot_ids is set from the validated output.

Each successful role commit is checkpointed, including partial progress inside the cinematography node. Resume skips committed role results, so finishing one scene before interruption does not repeat its inference. Completed workflow nodes are skipped as before. Cancellation checks run before state commit; an interrupted in-flight request may need resubmission, since the remote synchronous endpoint has no durable task query API.

## Cinematographer request sizing

Cinematographer context version 3 keeps the current Scene's creative intent separate from `CanonicalChainContext`. The latter is input-only, Core-authored, and carries the full authoritative initial/final state plus canonical entity IDs. The model also receives its screenplay scene, relevant local entity declarations/location, global VisualBible rules, StoryBible immutable/world facts and themes, and cinematography/hard constraints. Other scenes, full script, story acts/arcs, duplicated continuity, entity artifact references, and Scene shot topology are excluded. The first five roles' context policies are unchanged.

`scene_shot_budget()` is shared by ContextBuilder and semantic validation. Scene has no duration field, so remaining project duration is divided across unfinished scenes. Already committed scenes consume their actual shot duration/count. The scene maximum is min(floor(remaining shots / unfinished scenes), ceil(scene seconds / 4)), reserving capacity for every scene. Recommended minimum is min(maximum, ceil(scene seconds / 8)); it is guidance, not an invented narrative constraint. Remaining duration, shot budget, scene count and local min/max are supplied explicitly. Insufficient budgets fail before inference. Duration validation retains its 10% tolerance; the new allocation accounts for previously committed actual duration.

Inference records include transport failures independently of validation attempts. A read timeout records REMOTE_COMPLETION_UNCERTAIN and stops, requiring explicit resume rather than a blind second request. Committed scene results remain immutable and are skipped before context rebuilding. Existing checkpoints load with an empty inference_records list; no earlier role output needs regeneration.

The serving schema is constrained by the scene budget: `ShotPlanDraft.shots.maxItems` equals the current scene maximum. It uses `additionalProperties=false` throughout and dynamic enums for scene-local character, location, and prop IDs. It contains no `ContinuityChain`, canonical state snapshot, shot/chain ID, or previous/next topology, so the former ownership ambiguity is not expressible.

`CinematographerDraftMapper` first rejects unknown, ambient, cross-scene, type-mismatched, and conflicting duplicate local updates. It then starts from the complete Core-owned canonical state, applies only non-null local deltas, inherits omitted entities/fields, and deterministically creates shot IDs, chain ID, order, previous/next links, state_before, expected_state_after, retry policy, and quality policy. Existing `ShotPlan` semantic and continuity validation remains authoritative after mapping; no rejected model snapshot is silently repaired into canonical state.

## Explicitly authorized contract-schema revision

After the user grants an additional allowance, `authorize_contract_schema_revision(scene_id, authorization_reference)` can consume exactly one additional scene-scoped allowance per project. It requires an exhausted, uncommitted Cinematographer result with an earlier archived revision. It preserves the entire previous RoleResult in revision history, leaves earlier checkpoints/jobs/artifacts untouched, and creates a distinct invocation carrying `CONTRACT_SCHEMA_REVISION`, authorization reference, parent invocation ID, parent-result hash and request-schema hash. An immutable text artifact records the authorization and full request schema. The ordinary revision cap is unchanged.

The new invocation permits at most two structured repairs. Recurring chain-initial/local-state scope conflict is terminal immediately (`CONTRACT_STATE_SCOPE_CONFLICT`), even if repair slots remain; resume cannot bypass that stop. The runner also refuses an implicit schema change after authorization. A successful role follows the normal validation/commit/checkpoint path before the next scene can start. Existing successful scene results and earlier roles are not reopened.

## Request-contract type revision and deterministic replay

`authorize_request_contract_type_revision(scene_id, authorization_reference)` is a separate, single-use project operation. It requires the preserved `CONTRACT_STATE_SCOPE_CONFLICT`, archives the complete parent RoleResult, and records `REQUEST_CONTRACT_TYPE_REVISION`, parent invocation/result hash, old/new Schema hashes, ownership marker `CORE_CANONICAL_STATE_LLM_LOCAL_DELTA`, mapper version, authorization, and the two-repair cap in both checkpoint state and an immutable artifact. It does not reset ordinary retry history or reopen successful roles/scenes.

If an exhausted provider output failed only because deterministic mapper/validator code was defective, resume may revalidate that exact persisted raw output after the code fix. The replay emits an audited `role_output_validated` event with source request/output hash and mapper version, adds a `ValidationReplay`, and makes zero provider calls. Original attempts, validation reports, inference metrics, Job records, artifacts, and earlier checkpoints remain intact. This path cannot alter the raw output or bypass a remaining validation error.
