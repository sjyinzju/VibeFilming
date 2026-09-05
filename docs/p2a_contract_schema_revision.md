# Authorized CONTRACT_SCHEMA_REVISION — 2026-09-05

> Historical audit record. This revision stopped exactly as documented. A later, separately authorized `REQUEST_CONTRACT_TYPE_REVISION` changed ownership at the request boundary and completed P2A without modifying this record; see `p2a_verification.md`.

## Outcome

**Stopped as explicitly required; P2A is not complete or frozen.** The sole additional invocation for SCN-002-CABIN returned the same chain/local state ownership defect. The immediate-stop guard fired before any structured repair. Scene 3 and downstream mock production did not run. No further real request was made.

## Authorization and audit

- Kind: CONTRACT_SCHEMA_REVISION, not an ordinary retry reset.
- Exact scope: SCN-002-CABIN, the currently failed second scene.
- New invocation: project_9547865956164bbe822130793051b9f8:cinematographer:SCN-002-CABIN:revision1:contract_schema_revision.
- Parent: project_9547865956164bbe822130793051b9f8:cinematographer:SCN-002-CABIN:revision1.
- Previous RoleResult archived without modification, including attempts, validation reports, pending output, metrics and context/provenance references. All earlier checkpoints and artifacts remain in place.
- The authorization record includes the user authorization reference, parent-result hash, exact request-schema hash, repair cap of two and immediate state-scope stop rule.
- Immutable schema/authorization artifact: `workspace/p2a-real/artifacts/data/contract_schema_revision_SCN-002-CABIN/v1.json`.
- New allowance consumption persists in checkpoint; a repeated authorization command is rejected. Ordinary resume cannot bypass CONTRACT_STATE_SCOPE_CONFLICT.

## Actual request

| Metric | Value |
|---|---|
| Scene | SCN-002-CABIN |
| Context characters | 10427 |
| Estimated input tokens (characters/4 incl. schema/system) | 8766 |
| Actual prompt tokens | 3451 |
| Completion tokens | 6667 |
| Schema envelope characters | 22981 |
| Max output tokens | 12000 |
| Read timeout | 360s |
| Start / end UTC | 12:24:52.209 / 12:27:30.059 |
| Latency | 157.837s |
| Provider outcome / finish reason | success / stop |
| Real requests / structured repairs | 1 / 0 |
| Validation | failed; CONTRACT_STATE_SCOPE_CONFLICT |

The request schema hash matched the authorized strict schema. This was not a timeout, connection failure, truncated JSON, or mock fallback.

## Verified failure

- Canonical chain prop keys: PROP-001-CONSOLE and PROP-002-OVERRIDE.
- Scene-local allowed prop keys: PROP-002-OVERRIDE only.
- Returned Shot boundary maps correctly contained only PROP-002-OVERRIDE.
- Returned ContinuityChain.initial_state also contained only PROP-002-OVERRIDE, omitting the canonical console.
- Pydantic parsed the existing ShotPlan, but semantic validation rejected unequal chain initial state and the missing console at the scene ending.
- The strict request schema included a const equal to the full Director initial state; the returned value contradicted that constraint. The exact serving-side reason is not established from local evidence. No serving investigation/change was performed.

## Type-level analysis (proposal only; not implemented or rerun)

Separating request-level strong types is warranted. Both core fields currently parse as ContinuityState, while the adapter distinguishes their decoding schemas by adding ShotBoundaryState and a const beside the canonical state's reference. This protects the core through semantic validation but does not make the canonical-versus-local distinction explicit in the Python request-output type system. Merely renaming two aliases of the same dictionary schema would not address it.

Recommended next design:

1. ChainInitialStateDraft: distinct request-only type whose entity maps explicitly require every canonical boundary entity (including unchanged ambient entities). Derive required keys from the Director state; validate exact canonical equality locally, independently of serving const enforcement.
2. ShotLocalStateDraft: distinct request-only type whose closed maps allow only current Scene entity IDs and express the existing inheritance semantics. It must never substitute for the full chain initial state.
3. Generate constrained JSON Schema from these actual Pydantic draft types, rather than relying only on a post-export schema refinement of the shared core type. Preserve all cinematic Shot fields and the full canonical state contract.
4. Validate the draft, canonical equality, references and continuity before an explicit, lossless mapping into existing Shot/ContinuityChain IR. Never fill missing canonical entries into a rejected model response or weaken semantic checks.
5. Test both directions using this real failure as a fixture: local subset replacing canonical state, and canonical ambient entities leaking into local state. Distinct types do not guarantee LLM correctness; constrained decoding and independent local validation must both remain.

This is a request-contract clarification, not a reason to introduce two-pass shot generation, change the first five roles, alter Spark, or start P2B. Additional implementation/inference requires a new user instruction; no revision or repair budget was expanded here.

## Integrity and final verification

Before/after hashes verified all of the following:

- Complete RoleResults for the first five roles and committed Scene 1 unchanged.
- All previously committed output hashes unchanged.
- All previous revision-history entries unchanged.
- Previous Scene 2 failed RoleResult archived intact.
- All prior Job records/provenance unchanged.
- Exact authorized request schema retained.

Final local suite: **80 passed, 1 skipped** (the real integration probe remains opt-in). The sole real call above is the execution evidence for this authorization; no additional integration probe or real retry was run afterward.

Latest checkpoint: checkpoint_d861d6808f9345a8a1bd060ff31c0e5f in the same workspace/p2a-real. Valid committed real Shot count remains 1 (Scene 1). WORKFLOW_COMPLETED count remains 0. All failure evidence is preserved. No frontend or real media providers were added.
