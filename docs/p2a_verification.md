# Phase 2A Verification Record

Status: **PHASE 2A COMPLETE / FROZEN** on 2026-09-05. All five upstream reasoning roles and all three scene-level Cinematographer plans are real, while frame/image/video/critic/audio/post/final stages remain the intentional Phase 1 mocks. P2B, frontend, Spark/vLLM, and real media providers were not started or changed.

## Final accepted production

- Workspace: `workspace/p2a-real`
- Project: `project_9547865956164bbe822130793051b9f8`
- Final checkpoint: `checkpoint_2c67ce5973d94615ad2b90c57af4dfe6`
- Final selected artifact: `final_film@v1`, produced by `mock_final_render`
- Graph: all 21 nodes succeeded
- Durable completion evidence: exactly one `WORKFLOW_COMPLETED` event
- Real Cinematic IR: 3 Shots across 3 Scenes, 18 seconds total

| Scene | Accepted Shot | Duration | Real requests in accepted invocation | Repair outcome |
|---|---|---:|---:|---|
| SCN-001-CABIN | SCN-001-CABIN-SHOT-01 | 6s | 2 | initial truncated; repair 1 passed |
| SCN-002-CABIN | SCN-002-CABIN-SHOT-01 | 6s | 3 | two repairs consumed; exact final output passed deterministic replay after mapper fix, with no further inference |
| SCN-003-CABIN | SCN-003-CABIN-SHOT-01 | 6s | 3 | initial and repair 1 had endpoint-state conflicts; repair 2 passed |

Every committed plan was parsed by Pydantic, checked for local/entity/semantic rules, mapped to the existing Shot/ContinuityChain IR, and revalidated through the existing continuity engine. A final offline acceptance pass returned zero issues for all three plans.

## Final ownership contract

The accepted boundary is `FullCanonicalState(t) + ShotLocalDelta → deterministic mapper → FullCanonicalState(t+1)`. `CanonicalChainContext` is Core-owned input only. The LLM target is `ShotPlanDraft`, whose closed strict Schema contains creative Shot fields and `ShotLocalStateDraft` only. It cannot express canonical snapshots, continuity chains, shot/chain IDs, or previous/next topology.

The mapper rejects unknown, ambient, cross-scene, type-mismatched, and duplicate/conflicting local updates. Omitted local fields/entities inherit from canonical state. Core generates identity/topology and complete state_before/state_after snapshots, after which the unchanged semantic and continuity validators run.

The single-use revision record is `REQUEST_CONTRACT_TYPE_REVISION` with ownership `CORE_CANONICAL_STATE_LLM_LOCAL_DELTA`. It preserves parent invocation `...:contract_schema_revision`, parent-result hash `08211b5ec468df21a3d40ef7aa7194266311206f64fcda7a031df4d4ffc69bbc`, old Schema hash `958af680f11140b24e940d00b3c47affafcf30d23d09db329526301511e0eb13`, new Schema hash `8a3ded4a588fdd327c4f6730a54e96d6b2b2a88b9442b24bbb1dd5c1a3eac954`, and the authorized two-repair cap. The original authorization artifact records mapper v1. Real validation exposed a deterministic v1 representation bug: `_changes()` serialized nested `Vector3` values to dicts before unvalidated `model_copy(update=...)`, making equal positions compare as `dict != Vector3`. Mapper v2 preserves typed nested values.

Scene 2's final raw provider output (SHA-256 `fb653733d56f5442a014e9720dab461267f5baffc20001cb5826cf8030308407`) then passed an audited `DETERMINISTIC_CODE_FIX_REVALIDATION` under mapper v2. This replay submitted no provider request, did not edit the raw response, and preserved all three original failed reports and inference metrics.

## Final real request metrics

All rows used strict JSON Schema, max output 12000, and Cinematographer read timeout 360s.

| Scene | Attempt | Context chars | Input estimate | Actual input | Output | Latency s | Result |
|---|---:|---:|---:|---:|---:|---:|---|
| SCN-002-CABIN | initial | 10667 | 6694 | 3452 | 2122 | 50.278 | mapper v1 false position conflicts |
| SCN-002-CABIN | repair 1 | 10667 | 13241 | 10586 | 2044 | 51.048 | same deterministic defect |
| SCN-002-CABIN | repair 2 | 10667 | 13182 | 10505 | 2042 | 50.493 | replayed under v2 and accepted |
| SCN-003-CABIN | initial | 9671 | 6441 | 3141 | 1721 | 40.760 | location/global end-state conflicts |
| SCN-003-CABIN | repair 1 | 9671 | 12377 | 9588 | 1712 | 42.840 | global end-state conflict |
| SCN-003-CABIN | repair 2 | 9671 | 12370 | 9465 | 1699 | 41.967 | passed |

Scene 1 and the five upstream-role measurements remain in the historical sections below and in `p2a_cinematographer_verification.md`. None of those roles/scenes was reinferred during final completion.

## Integrity, resume, downstream, and tests

- The five upstream Role output hashes and Scene 1 Cinematographer output hash match the pre-revision baseline byte-for-byte at the canonical JSON hash boundary.
- All earlier Screenwriter/Scene 2 revision-history entries and all pre-existing Job/provenance hashes match the baseline. The failed `CONTRACT_SCHEMA_REVISION` parent was appended intact before the type revision.
- Final Project Shot 1 gained only expected mock-downstream frame anchors, last-frame artifact reference, and generation strategy; its preserved real Cinematographer Role output hash did not change.
- Event/request IDs prove final completion called only Scene 2's authorized invocation/repairs and Scene 3's invocation/repairs. Completed upstream roles and Scenes were skipped.
- Resume from the final checkpoint completed with 3 Shots and `provider_calls=[]`.
- Mock downstream created frame/video/critic/audio/post/final artifacts for all three real Shots; `final_film@v1` references all three mock videos.
- Full Python 3.12 suite: **87 passed, 1 skipped** in 10.27s. The one skip is the explicitly opt-in endpoint probe.
- Explicit real integration probe: **1 passed** in 1.11s, covering endpoint/model health, constrained completion, usage metadata, and Pydantic parsing.

## P2B handoff boundary

P2B can begin from the frozen FastAPI contracts for project lifecycle, workflow snapshots, typed Shot/job/artifact reads, human-review resolution, and cursor-based durable SSE replay/live events. The snapshot-plus-cursor rule is: fetch project/workflow state with its event cursor, then subscribe after that cursor. P2B must not infer provider/GPU state from UI state; current APIs and `EventEnvelope` remain authoritative.

## Historical pre-freeze record

The material below is retained unchanged as the audit trail for the earlier incomplete states. Its status statements are historical and are superseded by the completed acceptance above.

Historical status: full real production was **not yet accepted**. One real scene was committed. The user-authorized additional CONTRACT_SCHEMA_REVISION for Scene 2 repeated the canonical-chain/local-state defect and was stopped immediately after one request, without repairs. See [authorized revision report](p2a_contract_schema_revision.md) and [earlier Cinematographer verification](p2a_cinematographer_verification.md).

## Local verification (2026-09-05)

- Baseline commit: `9e18155` on `main`; original 37 tests passed and original mock CLI completed before implementation.
- Final supported runtime: Python 3.12.14 in `.venv312` (located in the bundled local runtime).
- Latest full offline suite: **80 passed, 1 skipped**. The skipped test is the explicitly opt-in real endpoint test.
- Real endpoint integration probe: **1 passed** while the tunnel was available. It verified configured model health and Pydantic-validated strict structured output.
- Python 3.12 editable package installation succeeded; dependency check reported no broken requirements.
- OpenAPI generated successfully: 15 paths, 69 schemas, including typed Shot/Project/Workflow/Job/Artifact response contracts.
- SSE tests exercise both durable replay after restart and an event emitted after an SSE connection is established.
- A reproduced and fixed provider-cache regression verifies that explicit resubmission can recover from a transient connection failure in the same process, without automatic retries or repeating successful requests.
- No model/serving infrastructure operation was performed. `.env` remains ignored and untracked; no secrets or reasoning fields are recorded.

## Real role execution

| Role | Result | Accepted invocation attempts |
|---|---|---|
| Creative Producer | Validated and committed; full downstream mock flow completed in the first vertical slice | 3 |
| Story Architect | Validated and committed | 1 |
| Screenwriter | Validated and committed after one diagnosed schema-policy revision | 1 in accepted revision |
| Visual Director | Validated and committed | 1 |
| Director | Validated and committed | 1 |
| Cinematographer | Request timed out; subsequent connection check was refused | No validated output |

Screenwriter's initial invocation exhausted its two repairs because defaulted scene character membership fields were omitted. The serving schema adapter now recursively requires object properties while leaving persisted contracts unchanged; error reports also list missing dialogue speaker IDs. The failed invocation remains archived. One explicit revision succeeded. Budgets are not automatically reset by resume.

The initial Creative Producer call also encountered a transport timeout. A later bounded-output invocation succeeded after two validation repairs. These are observed development results, not a claim that every large output is stable within the configured 180-second request timeout.

## Historical production state before the Cinematographer follow-up

- Workspace: `workspace/p2a-real`
- Project: `project_9547865956164bbe822130793051b9f8`
- Title: The Last Signal
- Typed outputs: CreativeDirection, StoryBible, Screenplay, VisualBible, 3 Scene objects
- Catalogs: 2 characters, 2 locations, 2 props; 3 screenplay scenes
- Shots: 0 validated real shots so far
- Failed node: `shot_planning`
- Latest real checkpoint: `checkpoint_b14e7bb2b9cc4afa9f0f48033be9516d`

The real workspace was seeded from the accepted Creative Producer checkpoint in `workspace/p2a-A`, retaining its immutable artifact URIs and provenance. This avoided repeating successful inference. Later stages were resumed in separate processes, with no repeated calls to completed roles.

The original follow-up command was:

```powershell
.\.venv312\Scripts\python.exe scripts/run_p2a.py --workspace workspace/p2a-real --resume
```

That follow-up has now executed without transport timeouts. Do not repeatedly run this command against the exhausted Scene 2 result: ordinary resume intentionally makes no new inference after the repair budget is spent. See the follow-up report for the current blocker. Earlier roles and the committed first scene must not be reopened.

## Remaining acceptance work and limits

1. Complete real Cinematographer output, semantic/continuity validation, and final `WORKFLOW_COMPLETED` in the same production.
2. Confirm real post-cinematography resume and resulting media placeholders against that completed run.
3. Large nested outputs may approach the configured timeout; this has not been resolved or hidden.

Deterministic semantic checks cover references, state transitions, budgets and explicit commitments. They cannot prove arbitrary natural-language constraints; those remain reported for human review. Local backend task arbitration and journal writing require a single worker. Local cancellation does not guarantee remote inference termination. These limits are documented in `role_runtime.md`, `llm_provider.md` and `p2a_runtime.md`.
