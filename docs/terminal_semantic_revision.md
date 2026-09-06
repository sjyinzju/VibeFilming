# P2B Scene 3 terminal semantic repair — 2026-09-06

Project: `project_532cb7aae9074c6b9d6887dcabdbb2a8`.

## Evidence and root cause

The immutable baseline is checkpoint `checkpoint_eb6fbaf080d748c5aeda7ff6c593c091`.
All three original raw responses were recovered from existing checkpoint versions.
Both repair prompts were reconstructed from their saved context, previous raw output,
validation report and strict schema. Both reconstructed prompt hashes match the
original RoleAttempt.prompt_hash. No network/timeout diagnosis was repeated.

| Layer | Kael emotion |
| --- | --- |
| Director initial | `Focused.` |
| Director canonical final | `Protective, relieved, vulnerable.` |
| Raw performance.emotion_start, all three attempts | `Protective, relieved, vulnerable.` |
| Raw performance.emotion_end, all three attempts | `Safe, connected, calm.` |
| Raw local_state_delta character_updates for char_kael | `Safe, connected, calm.` |
| Mapper / Shot.expected_state_after | `Safe, connected, calm.` |
| Validator | observed `Safe, connected, calm.`; required `Protective, relieved, vulnerable.` |

`Safe, connected, calm.` is **Elara's** canonical final emotion. The model used
Kael's target as his starting emotion, then assigned Elara's ending to both people.
Attempt 0 was also only 6 seconds; Attempts 1 and 2 were 30 seconds and have the
same raw output hash:
`91177649e82c84bfe6a9ed1b63ccab51d972ce7061e1af6f4d77ab444a807587`.

Classification: A is directly confirmed. B and C are contract/repair weaknesses:
the full canonical target was supplied, but no typed current-to-terminal diff or
editable local path was provided. The generic repair pointed to `scene_3-CHAIN`
and said to correct the errors in the same full schema. D is ruled out: the mapper
faithfully preserved the wrong raw value. E is not a synonym case: these are
different characters' distinct emotional targets. For this emotion, F is not
supported: the screenplay's rescue, shielding, trembling and vulnerable contact
are compatible with protective/relieved/vulnerable. This does not claim a general
automated proof of all story prose or subjective cinematic quality.

## Implementation

- Input-only `RequiredTerminalDelta` is computed by Core, by entity identity and
  field, with exact observed/required values and explicit editable local paths.
  Full canonical chain context remains input-only. Frozen Cinematic IR is unchanged.
- A terminal-only validation failure routes to strict `TerminalRepairDraft`:
  only the final shot's performances, local_state_delta and frame_planning are
  editable. Core retains timing, camera, narrative, commitments, topology and
  every other shot, merges the complete editable fields, then validates the full
  reconstructed ShotPlanDraft → mapped ShotPlan → semantic/continuity → commit.
  It does not auto-correct or reinterpret canonical targets.
- `SEMANTIC_CONTRACT_REVISION` binds parent invocation/result hash, source raw
  output hash, repair schema hash and authoritative terminal target hash. There
  is one new invocation per failed scene, at most two structured repairs, and no
  implicit extra allowance after exhaustion. Source/schema/target tampering is
  rejected before inference. New attempts persist their raw structured response
  and request prompt for audit; no chain-of-thought is requested or recorded.
- Old RoleResult is retained in role_revision_history and in an append-only
  provenance artifact; old checkpoint files remain. Ordinary Resume rejects an
  exhausted invalid output unless deterministic replay now validates it.
- Studio has a typed eligible scene projection and explicit revision command.
  Semantic failures show actual validation issues and a labelled revision action;
  uncertain completion remains explicit rerun, transport/SSE failures remain
  reconnect. React guidance kept this derived policy outside Inspector and avoided
  timer-based failure inference or duplicate recovery state.

## Actual run and validation

Following targeted tests and full pytest, **unchanged Attempt 2 was replayed
read-only again**. It still failed exactly the emotion conflict. No replay was
misrepresented as a code fix and no old raw output was edited to make it pass.

Only new real request:
`project_532cb7aae9074c6b9d6887dcabdbb2a8:cinematographer:scene_3:revision0:semantic_contract_revision_0`.

- Start/end: 2026-09-06 06:19:48.786 UTC → 06:20:15.702 UTC.
- Latency 26.8998 s; prompt tokens 7110; completion tokens 1079; finish `stop`.
- Streaming, inactivity 60 s, total budget 480 s. No transport changes in this task.
- One invocation, **zero** structured repairs. Real output committed.
- Duration: **30 s**. Semantic issues: **0**. Continuity conflicts: **0**.
- Kael's returned emotion_end and mapped terminal emotion are now
  `Protective, relieved, vulnerable.`.
- Final canonical state equals Scene.expected_final_state after excluding only
  runtime `previous_shot_id` / `last_frame_artifact_id` metadata.
- Parent result equality and parent hash both verified against the baseline.
- All seven previous committed outputs and their inference_records are unchanged:
  Creative Producer, Story Architect, Screenwriter, Visual Director, Director,
  Scene 1 Cinematographer and Scene 2 Cinematographer. No repeated inference.
- Commit/checkpoint confirmed at `checkpoint_1a19efbb9a9d433eb3f9be84ea57645c`.

The current real workflow has advanced from failed shot_planning to
**WAITING_HUMAN at shot_gate**. Pending review:
`review_65b1aadc81ce4b149e6972a903ef2048`.
The three real ShotPlan subjects auto-open in Studio and their displayed Raw JSON
matches committed output. Read-only browser acceptance sent **zero commands**.
Human approval has not been fabricated; the actual downstream Mock media pipeline
has therefore **not yet started**. It can proceed through existing Approve & Resume.
This is not a claim of WORKFLOW_COMPLETED. No P3 work was started.

## Verification

- Backend: **112 passed, 3 skipped** (opt-in real endpoint probes; this task's
  authorized real revision was executed separately as reported above).
- Frontend: **27 passed**; TypeScript/Vite build passed (existing large-chunk warning).
- Isolated P2B browser regressions: **7 passed**, including SSE recovery and full
  Mock pipeline with test-only explicit review approvals.
- Read-only live browser: three real review outputs matched, review auto-opened.
- New regression coverage includes typed terminal diff, final-shot-only patching,
  explicit API authorization, wrong-scene/blank authorization rejection, source
  tampering, bounded repairs, durable parent lineage, predecessor preservation,
  UI issues/action policy, and no implicit Resume allowance.
