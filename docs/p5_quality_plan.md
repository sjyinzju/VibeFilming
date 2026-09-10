# P5 incremental quality plan

2026-09-09. Scope: isolated SHOWCASE hero film; preserve all P4B/C/D workspaces,
artifacts, and existing uncommitted implementation. No model downloads or P6.

## Verified integration points

- `Evaluation` already pins artifact versions; `VisionInspectionResult` owns observed
  evidence, version/hash and deterministic adjudication. Aggregate these, never invent
  another critic result or speech-recognition score.
- Character, location and prop contracts currently contain unversioned artifact IDs;
  `MediaReference` supports version/hash. Add selected, reviewed identity packs and a
  deterministic shot-scoped resolver with mandatory exact pins.
- `MockMovieProduction` is also the shared real production engine. Storyboard creation
  precedes H3; currently no image consistency gate. `cinematic_critic` is still a mock.
- `run_repair_loop` currently returns on the first human escalation. P5 must isolate
  blocked shots and continue independent work without recording human approval.
- Existing P4A manages Qwen/FLUX/H3/VLM/TTS/music. Kontext needs an isolated capability
  and memory probe before admitting production work; unknown memory fails closed.
- P4D records real TTS/music/post but unresolved human feedback: native speech overlap
  and Ella's young timbre. Apply mature VoiceDesign to the new project, preserve old film.

## Implementation / verification sequence

1. Probe the downloaded Kontext layout and installed ARM64/GB10 runtime, read-only.
   Establish whether offline execution is possible; never infer compatibility from size.
2. Add quality profiles/aggregate reports, exact reference packs/resolver, durable
   bounded compute ledger and targeted repair cost policy; targeted mock tests.
3. Add verified Kontext provider/service and P4A integration if the local files/runtime
   support it. Isolated before/after smoke, real VLM review and measured memory profile.
4. Integrate frame gate, observed-evidence cinematic/film review, scoped repair and
   resume into existing engine/DAG; independent shots continue on human review.
5. Add Studio quality/reference/history views and candidate export/morning review.
6. Run focused tests during development, then one full regression/build/browser pass.
7. Explicit real opt-in: baseline old film without mutating it; new psychological
   science-fiction project, 2–4 scenes / 10–14 shots, 90–120 s target, >=75 s minimum.
   Review references before H3; TTS is authoritative dialogue; scene-level instrumental
   music. H3 total <=18, per-shot repair <=1, frame edit <=2, inspection revisions <=3,
   TTS attempts <=2/cue revision, music automatic repair <=1/scene.
8. Export only technically passing accepted footage. A final aesthetic gate can stay
   pending for `technical_candidate_final`; never mark it approved. Report exact hashes,
   downloads, actual metrics and unmet DoD honestly. Stop after P5.

## Evidence policy

No ASR/lip-sync: intelligibility/speaker/lip synchronization cannot be machine-passed
without evidence. Human listening remains pending. No claim of improved quality until
baseline and candidate evidence support it. Runtime/telemetry unknowns remain unknown.
