# Cinematographer follow-up verification — 2026-09-05

> Historical audit record. All failures and measurements below remain authoritative for the earlier contracts. A later `REQUEST_CONTRACT_TYPE_REVISION` completed Scenes 2 and 3 and froze P2A; see `p2a_verification.md`.

Historical follow-up record. The additional user authorization was subsequently consumed once and stopped on recurrent state-scope conflict. See [authorized revision report](p2a_contract_schema_revision.md) for the latest outcome; no additional retry is authorized.

Status: **not frozen / not WORKFLOW_COMPLETED**. The 180s read-timeout blocker is resolved for the measured requests. Scene 1 has one validated, committed real Shot. Scene 2 exhausted initial + two repairs and then its one explicit revision (again initial + two repairs). Scene 3 has not been invoked. No additional budget was granted or silently reset.

## Original request inspection (before timeout changes)

- Scene: SCN-001-CABIN, THE LOOP. Generation was already scene-level.
- Global timeout 180s; actual role/global output ceiling 16000 tokens, not 8000.
- Project target 18s, 3 scenes, max_shots 6; current Scene has no independent duration field, so allocation was 6s / max 2 shots.
- Context fields: brief, current scene, current screenplay_scene, full story_bible, visual_bible, relevant characters/locations/props, duplicate continuity, scene_duration_budget, scene_shot_budget.
- Context 13641 characters; system 984 characters; schema response-format envelope 18732 characters. Conservative input estimate 8340 tokens using ceil(total characters / 4), not a tokenizer measurement.
- User-supplied serving logs showed uninterrupted generation near 41–42 tokens/s over 180s, no queue/OOM/crash. A 16000-token allowance can take about 390s at that rate. The measured follow-up responses confirm that valid HTTP responses can take longer than 180s.

## Implemented policy and scope

- Model-agnostic RoleInferencePolicy outside Domain; RoleDefinition overrides; default read inherits MOVIE_AGENT_LLM_TIMEOUT.
- Cinematographer read 360s; connect/write/pool 10/30/10s. Other five roles retain their previous read/output policies.
- Deterministic remaining-duration and remaining-shot allocation shared by context and validation. A 6s scene recommends 1 shot, max 2. Output ceiling min(global, 12000, 2000 + 5000 × max shots).
- First context reduced to 10593 characters. Other scenes, story acts/arcs, duplicate continuity and entity artifact references are excluded; relevant canon/constraints and current scene state remain.
- Read/write uncertainty is non-automatically-retryable REMOTE_COMPLETION_UNCERTAIN. Explicit resumed requests can opt into resubmission; no blind read-timeout retry, no remote cancellation guarantee.
- Request metrics include role/scene, chars, estimate, schema chars, effective budgets, timestamps, latency, outcome, actual token usage, validation, and (for the second process) finish_reason. No reasoning is stored.

## All real Cinematographer requests in this follow-up

Every row used max_output_tokens=12000 and read_timeout=360s; all returned successful HTTP/provider responses. Times below are inference latency, not total workflow time. Input/output columns are server-reported token usage.

| Scene / invocation | Attempt | Context chars | Input estimate | Actual input | Output | Latency s | Validation |
|---|---:|---:|---:|---:|---:|---:|---|
| SCN-001-CABIN / revision0 | initial | 10593 | 7654 | 3398 | 12000 | 287.20 | Truncated JSON after repeated chains |
| SCN-001-CABIN / revision0 | repair 1 | 10593 | 22699 | 21131 | 4264 | 111.41 | Passed and committed |
| SCN-002-CABIN / revision0 | initial | 10427 | 7613 | 3384 | 7600 | 181.80 | Outside-scene prop in Shot states |
| SCN-002-CABIN / revision0 | repair 1 | 10427 | 19878 | 16846 | 4331 | 110.97 | Same prop-scope failure |
| SCN-002-CABIN / revision0 | repair 2 | 10427 | 16932 | 13405 | 4330 | 108.22 | Same prop-scope failure; exhausted |
| SCN-002-CABIN / revision1 | initial | 10427 | 7719 | 3451 | 7346 | 175.29 | Same prop-scope failure |
| SCN-002-CABIN / revision1 | repair 1 | 10427 | 19831 | 16872 | 3783 | 97.09 | Removed ambient prop from chain too |
| SCN-002-CABIN / revision1 | repair 2 | 10427 | 16522 | 12950 | 4245 | 106.05 | Reintroduced prop in Shot states; exhausted |

Repair input estimates include prior final output, errors and target schema. Initial six requests used schema envelope 18732 chars; revision1 used 18792 chars with array bounds. The last state-scope refinement below was implemented after these requests and is not claimed real-verified.

First-scene artifact: SCN-001-CABIN-SHOT-01, duration 6s, one continuity chain. Initial output contained one shot but repeated the same chain eight times before truncation. No partial JSON was committed. The first repair produced a complete candidate accepted by Pydantic and semantic/continuity checks.

The second process skipped the committed first scene. The five earlier role output hashes were compared before/after and are unchanged. No first-five inference was repeated. The initial call explicitly resumed the earlier failed production; subsequent calls were bounded validation corrections, plus one separately archived policy revision, not concurrent duplicates or transport auto-retries.

## Confirmed remaining issue and implemented guard

SCN-002-CABIN.prop_ids contains only PROP-002-OVERRIDE. Its immutable Director initial/final state also carries an unchanged PROP-001-CONSOLE. Existing contracts require the full initial state in the continuity chain but restrict Shot state maps to scene membership. The core can correctly inherit unchanged ambient state; it must reject invented or silently changed ambient values.

The LLM oscillated between copying the console into Shot maps and deleting it from the chain, despite exact correction paths. No Director output, user constraint, validation rule, or real candidate was manually altered.

The final adapter refinement now encodes these two existing invariants separately: closed entity-key maps for ShotBoundaryState and const=Director initial_state for the chain. Pydantic/semantic validation still runs afterward. Offline tests verify that the original persisted ShotPlan schema is unchanged and ambient-state changes still fail validation. Real serving compatibility and effectiveness of this final refinement remain unverified because the permitted explicit revision allowance is exhausted.

## Current checkpoint and next decision

- Workspace: workspace/p2a-real (same original project).
- Latest checkpoint: checkpoint_95c151f06c7e49aa82f69ce3527aca76.
- Committed real shots: 1; current failed node: shot_planning; WORKFLOW_COMPLETED events: 0.
- Downstream remains mock, but has not run after all real scenes in this workspace.
- Preserve the three archived revision0 failures and three current revision1 failures.
- A further real run needs explicit user authorization for one additional, audited Scene 2 revision with the new schema constraints. Ordinary resume must not reset the budget. No additional allowance or bypass flag has been implemented.

## Verification

- Python 3.12 full suite: **76 passed, 1 skipped**.
- Explicit real endpoint integration probe rerun after the production stopped: **1 passed** (0.87s). This small probe is not a full ShotPlan/state-scope verification.
- Tests cover role defaults/overrides, split HTTP timeouts, output ceilings, scene allocation, schema array/state bounds, no schema downgrade, uncertain-result caching/no blind retry, persisted repairs, scene checkpoint/resume and no repeated inference for completed scenes.
- No Spark/vLLM changes, no frontend/P2B, no image/video/VLM/audio integration, no secret changes.
