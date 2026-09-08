# P4B — Qwen3-VL critic and human guided repair

Status (2026-09-08): **P4B DoD complete**. Real CLI and browser inspection committed
immutable evidence; real browser UI/SSE/refresh and human-contract checks passed.
The original single-worker 8082 Studio backend has been restored with exclusive
P4A ownership and zero active leases. Real CLI inspection used the
existing Scene 01 video and first/last frames. The user authorized a temporary 8082
shutdown and stopped that worker; every acceptance process takes the normal P4A
ownership lock. No ownership, state or checkpoint file is deleted or bypassed.

## Provider and transport

`Qwen3VLVisionProvider` implements the existing VisionProvider interface. Configuration
selects `qwen3_vl`; the default remains Mock. No real failure falls back to Mock.
The direct request is `POST /v1/chat/completions`, model `movie-agent-vision`.
The deployed OpenCV/FFmpeg build cannot open the original H.264 MP4: the staged
file's hash and size match, but `VideoCapture.isOpened()` is false and vLLM returns
`Could not open video stream`. No container, codec package or model was changed.
The explicitly configured `jpeg_sequence` transport uses
`video_url: {url: data:video/jpeg;base64,<JPEG1>,<JPEG2>,...}` with immutable first/last
reference `image_url: file:///media/<sha256>.png` entries in the same user message.
The original video is still staged and hash-verified. These are deterministic decoded
inspection frames, not regenerated production media. `native` transport remains an
explicit alternative for deployments with compatible decoders; there is no silent
format retry or Mock fallback. Browser requests only reach FastAPI.
The prompt contains canonical Shot fields, requirements, requested profiles, output
language, source duration and sampling facts. It explicitly forbids invented story
facts, vendor settings and canonical modifications.

`SparkMediaStager` resolves real bytes from the existing stores, verifies MIME,
signatures, size and SHA, and invokes a constant SSH transfer protocol. The API
accepts bytes/MIME, never remote commands. It uses content-addressed names, remote
checksum verification, atomic publication, locking, deduplication, a 24-hour TTL,
256 MiB item cap and 8 GiB total cap. Cleanup only touches recognized staging files,
never Artifact history. A full cache fails rather than evicting recent active input.
Persistent production JSON uses opaque Artifact URIs; no Windows media path is added.

## Verified deployment facts

- `/health`: HTTP 200; `/metrics` has vLLM running and waiting gauges, both zero
  at the read-only capability check. No repeated model inventory verification.
- NVIDIA image contains vLLM `0.27.1+93523f72.dev`.
- Installed `VideoMediaIO` accepts `media_io_kwargs.video.num_frames`; installed
  OpenCV loader uses uniform `np.linspace(0, total_frames-1, count, dtype=int)`.
- Request policy: FAST 24 frames, FULL 40 frames, explicit OpenCV backend.
  The deployed compatibility path locally decodes those exact uniform source indices
  into 640-pixel-wide JPEGs. vLLM's installed `VideoMediaIO.load_base64` accepts
  `video/jpeg` plus `frames_indices`, `fps`, `total_num_frames`, `duration` and
  `do_sample_frames=false`; this preserves source time and prevents resampling.
  TARGETED additionally extracts at most eight deterministic JPEG samples around
  supplied problem intervals, labelled with original source times and hashes.
  Repaired versions automatically reuse accepted issue intervals for targeted review;
  extraction clamps an end-of-video interval to the last available source frame.
- JPEG sequence extraction validates the exact sample count and records each JPEG
  hash plus a sequence hash. Native decoder counts remain explicitly labelled as
  requested bounds. Neither mode claims exhaustive frame review; temporal evidence
  is approximate between sampled frames.
- The GPU-less render endpoint confirmed the JPEG sequence plus reference images
  preprocess successfully (HTTP 200); its large feature response was not retained.
  Real CLI inference also accepted mixed image/video and strict JSON schema, returning
  one complete response with `finish_reason=stop`. The adapter uses Pydantic
  `response_format.json_schema` by default;
  `json_object` is an explicit configuration alternative, with the same strict parser,
  semantic validator and at most one targeted structured-output repair. No regex JSON
  extraction, thinking-tag stripping or unbounded retry is implemented.

## Source artifact and staging evidence

Source project: `project_3effeb45f3844bf89c2c96e83770114b`.

| Input | Exact version | SHA256 |
| --- | --- | --- |
| `video_SCENE_01-SHOT-01` | 1 | `20466de1f9e2b8daaf934c41c2eb0e5b8e61d8c16c25f05fc75c8c1202227d7f` |
| `frame_SCENE_01-SHOT-01_first` | 1 | `96357d76d811d764a0e40f6af6eb2923c7dbe6d55c3510b9da2f5d466cef3f06` |
| `frame_SCENE_01-SHOT-01_last` | 1 | `a680c563d03eb4012caa3fed9d30ca2ba87ef7b02ade0af52a04bac4711d428b` |

The source manifest declares 15 seconds / 360 frames. ffprobe measured **15.083333
seconds / 362 frames**, 24 fps, 1024×576. Inspection uses these actual decoder facts;
the original Artifact metadata remains untouched. Video size is 2,052,328 bytes.
Staging/prepared-request records are in `workspace/p4b-media-staging.json` and
`workspace/p4b-capability-request.json`. These are transport checks, not AI results.

## Core ownership and evidence

The internal Draft contains only scores, issues, evidence, proposed decision and
summary. It derives nested fields/enums from the existing contracts. Core injects
request/project/shot/artifact/provider/model identity, version, SHA and provenance.
Validation enforces requested unique profiles, 0..1 scores, real-duration time ranges,
valid frame references, evidence for major/critical issues, compatible actions and
no PASS with unresolved blocking issues. Default required score is 0.80; global and
per-profile thresholds are configurable.

Core commits PASS only with adequate evidence, all requested profiles above threshold
and no blocking issue. Targeted supported problems route to REPAIR, a fresh video to
REGENERATE. Missing/uncertain evidence, unsupported actions or exhausted budget route
to HUMAN_REVIEW. The proposal cannot override this policy.
Executable repair actions come from the configured provider capabilities and are
included in the critic cache configuration.

Every inspection is an immutable structured Artifact in the existing ArtifactStore.
Parent URIs pin video/first/last/reference versions. Provenance carries SHA, staging,
sampling, prompt/schema version, model, request identity, timestamps and latency.
Successful result caching includes artifact version/hash, profile set, explicit
inspection revision, expected context and critic configuration. Switching from Mock
to Qwen3-VL cannot reuse Mock evidence. Crash recovery reads committed artifacts even
when the last checkpoint predates the commit.

## Repair and human review

The original DAG is unchanged. `repair_accept` contains a finite checkpointed state
machine. It allocates a new version, compiles preserve/fix/avoid/evidence and raw human
feedback into GenericVideoPromptCompiler, generates, runs technical QC, re-inspects,
runs cinematic QC, and accepts or escalates. Frame regeneration routes through the
existing image provider and binds the new anchor version into the next video.
Unsupported image edits/reference-strengthening/splitting are not falsely executed.

`HumanRepairDirective` extends the user command with Core identity and timestamps.
It preserves exact target version/SHA and raw feedback plus accepted/dismissed issue
IDs, preserve requirements and change requests. KEEP_CURRENT selects the video and
records a visible override without fabricating AI PASS. APPLY_AI_REPAIR accepts the
available plan. REGENERATE uses the same canonical Shot. CUSTOM_REPAIR compiles user
feedback into the same repair pipeline. Each directive authorizes one explicit extra
attempt; subsequent automatic attempts retain the original bounded budget.

Only actual escalation pauses at HumanReviewRequest / AGENT_ESCALATION. PASS does not
block. Proactive feedback before Final Gate creates the same directive; a later safe
media revision invalidates only repair_accept and its descendants. Earlier planning,
frames, successful generation and all history remain intact. Running downstream work
must be paused first. Completed-project reopening is explicitly unsupported.

The Inspector shows exact-version video, provider/model, decision, profile scores,
issues/evidence/times and timestamp seeking. Human controls submit durable commands.
Final Gate displays human overrides. Existing durable events/SSE refresh TanStack
snapshots; no extra top-level pipeline or fake VLM internal execution graph is added.

## Acceptance commands and current limits

Deterministic tests: `tests/test_p4b_vision.py`, `test_p4b_repair_loop.py`,
`test_p4b_human_api.py`, `test_p4b_resources.py`, and frontend `media-review.test.tsx`.
They cover contracts, transport, malformed output, semantic rejection, lease safety,
bounded repairs, all four human actions, proactive PASS feedback and restart reuse.

Verified on 2026-09-08: full backend **305 passed, 4 opt-in tests skipped**; P4B subset
**49 passed**. Full frontend **43 passed**, production build passed, and all **9**
existing Studio/locale browser regressions passed. The isolated fake
browser command `npm run test:e2e:media` passed: pending human review, four controls,
custom feedback preserved verbatim, v2 generation and PASS reinspection, durable
events, unchanged StoryBible, preserved v1 and review recovery after refresh.
Its screenshot is `workspace/p4b-vision-acceptance/fake-human-review.png`.
Windows key-setup tests ran with a process-scoped execution-policy preference only;
no system policy was changed. The existing >500 kB Vite chunk warning remains.

Real CLI: `.venv312/Scripts/python.exe scripts/accept_vision.py --run`.
Real browser: set `MOVIE_AGENT_RUN_VISION_INTEGRATION=1`, then in `web` run
`npm run test:e2e:vision`. The opt-in server copies the existing project to an isolated
acceptance workspace, runs only Scene 01 inspection through P4A, retains SSE evidence,
and checks playback, provider, Models, refresh persistence and no browser-to-8001 calls.
It must be the sole local owner of P4A; it does not bypass the ownership lock.

## Real CLI evidence

`workspace/p4b-vision-acceptance/acceptance-r1.json` records a real `qwen3_vl` /
`movie-agent-vision` result. Prompt `p4b.1` returned a REGENERATE proposal; Core chose
HUMAN_REVIEW because top-level evidence was missing. Issue evidence is retained; the
system neither fabricated missing evidence nor accepted the model proposal blindly.

Actual summary: “视频内容与预期严重不符：未出现林恩角色，未执行关键动作，环境光线和镜头运动均不符合预期。
画面中出现的男性角色和城市街道场景与预期的悬浮区走廊场景不匹配。” These are model assessments,
not verified character identity. The first review has no structured time ranges.

| Requested profile | CLI score |
| --- | ---: |
| video_quality | 0.85 |
| prompt_alignment | 0.65 |
| action_completion | 0.55 |
| camera_motion | 0.95 |
| continuity | 0.90 |
| artifact_detection | 0.95 |

Issues: major `action_incomplete`, minor `lighting_mismatch`, minor
`camera_motion_mismatch`. HTTP inference took **55.4234 s**, provider staging plus
inference **62.5366 s**. Usage: 9,542 prompt + 903 completion = 10,445 tokens;
one completed attempt, no separate reasoning field or structured-output retry.
The exact request body is `request-r1.json`; no API credential is stored there.

Artifact `artifact://inspection_SCENE_01-SHOT-01_v1/v1`, SHA256
`38776ba259129bea13c9dcd87f2366d5c02f909b582009e98d62c4adc4a060ef`, source job
`inspect:SCENE_01-SHOT-01:v1:db7728dd7e2b2adf`. The source video version/hash above
and both reference hashes are pinned in provenance. FAST samples source frames
0,15,31,47,62,78,94,109,125,141,156,172,188,204,219,235,251,266,282,298,313,329,345,361.
JPEG sequence SHA256: `a4f0efeb0f7e768d4be7a3440485cb7ee44d817b7a5dcb9ec0ab67de1f26937c`.

## Calibration and failure evidence

P4A telemetry measures `/proc/meminfo` unified memory, sampled during startup and
inference. Cold baseline is **117.4598 GiB available**, before startup **117.4568**,
model ready **31.8074**, before inference **31.7790**, sampled minimum **31.0410**,
after inference **31.1306**, after lease release **31.1333**. The successful CLI's
sampled incremental high-water is **86.4188 GiB**. An earlier cold-start/decoder-rejected
attempt sampled **87.74 GiB**, and is retained in `telemetry-native-request-rejected.json`.
These are sampled host-pressure deltas, not exact CUDA allocation peaks.

The initial provisional 108 GiB ceiling plus 12 GiB headroom correctly failed cold
admission at 117.9 GiB available, before inference. A calibration-only 105 GiB ceiling
retained all headroom. Measured defaults are now **88 GiB resident / 92 GiB peak**,
with the separate **4 GiB system reserve + 8 GiB safety margin** unchanged. Basis:
round the measured startup high-water up, then retain 4 GiB profile allowance. No
estimate derives from model file size. FULL/targeted workloads remain unbenchmarked.
VLM uses one exclusive lease, warm affinity and normal idle TTL. No OOM was observed.

A browser attempt returned HTTP 200 but an optional post-response telemetry timeout
discarded its response in the acceptance wrapper. P4A quarantined that request;
metrics later proved running=waiting=0, and normal controlled stop plus reconcile
released lease `lease_170a9ffd309545f9ab2bb76af219e9c0` at 12:56:20 UTC. No blind retry
or direct state edit was used. The wrapper now saves returned output before optional
telemetry and records missing telemetry without turning it into a model failure.
A separate pre-inference resource error exposed an invalid preparation retry
transition; the executor now retries preparation in place, with regression tests
covering recovery and exhausted retry budget without an extra provider call.

No Qwen reasoning, FLUX or H3 inference was repeated. Acceptance installs fail-fast
guards on upstream providers and compares non-Vision job IDs. Independent
TTS/music/SFX/Foley, cinematic critic and final post remain Mock. P4C is out of scope.

## Real browser, human contract and final restoration

The browser-triggered `visual_semantic_critic` executed another real inspection with
prompt `p4b.2`. That prompt explicitly asks for supported top-level observations and
structured source time ranges, while retaining the instruction that a faithful shot
can PASS and no defect should be invented. The result proposed REGENERATE; Core chose
**HUMAN_REVIEW** (`unsupported_or_unspecified_repair`), because the issues did not
specify executable repair actions.

Actual summary: “视频内容与预期严重不符，未呈现悬浮区环境，未出现林恩角色，关键情节缺失。
场景、角色、动作均与预期要求不符，无法验证任何预期元素。” Scores, in the same six-profile
order as the CLI table: **0.85 / 0.75 / 0.65 / 0.95 / 0.90 / 0.95**. Issues are major
`action_incomplete`, critical `missing_character`, critical `scene_drift`, each with
model-supplied **0–15 s** ranges, plus seven top-level evidence statements. These are
approximate sampled-video assessments. Character identity and the model's statements
about an alarm require human verification; this visual transport does not inspect
audio. The model's scores and narrative are preserved without claiming they are
objective ground truth or silently converting them into PASS.

Actual HTTP inference: **32.3003 s**; provider preparation plus inference: **39.4411 s**.
9,588 prompt + 767 completion = 10,355 tokens, one completed schema-constrained
attempt, `finish_reason=stop`, no separate reasoning output. Available-memory cold
baseline **117.8726 GiB**, model ready **31.5383**, before inference **31.5783**, sampled
minimum **31.3533**, incremental sampled peak **86.5193 GiB**. Post-inference SSH
telemetry had gaps; the complete seven-phase calibration is the earlier CLI run.
The old P4A observation recorded zero when all inference-period samples were missing;
that historical entry is retained but **must not be interpreted as a measurement**.
The coordinator now records `null` for absent peak measurements, with a regression
test. The configured conservative profile uses the actual acceptance samples.

Final Artifact: `artifact://inspection_SCENE_01-SHOT-01_v1/v2` (inspection version 2,
still source video version 1), SHA256
`6053decc31457913abff6d5aadc980c1be62396aabd6f63ed4ab11de9340bf1b`, result
`visionresult_cd97c7df52004734a7d3f3380cd540ff`, source job
`inspect:SCENE_01-SHOT-01:v1:3f3799616abea94c`.

The initial browser execution committed this Artifact and released its lease, but
the acceptance report's final optional OOM query timed out. That query now records
unknown telemetry instead of failing a committed review. The successful final
`npm run test:e2e:vision` (**1 passed, 8.7 s**) reused this committed real result,
without another inference. It verified the actual scores, issues, evidence and time
buttons, playable exact-version video, VLM lifecycle badge, `qwen3_vl` model identity,
no Mock badge on the real review, no browser request to port 8001, durable SSE and
review persistence after refresh. Cached UI replay preserves the real latency and
calibration data. It does not claim its 8.7-second UI time is model inference time.

The real result was passed through existing `HumanRepairInput` → AGENT_ESCALATION
`HumanReviewRequest` → immutable `HumanRepairDirective`, using a clearly labelled
KEEP_CURRENT test fixture. The directive pins the real result and source SHA, retains
feedback verbatim, leaves StoryBible unchanged and creates **zero inference jobs**.
`human-contract.json` records this proof. All four dispositions and proactive feedback
on PASS remain covered by deterministic tests; fake repair browser E2E passed again
(1 test). No real H3 repair was run.

There were three completed VLM HTTP inference responses in total: CLI v1, one response
lost by the former telemetry wrapper, and the committed browser v2. Native MP4 was
rejected before decoding/inference. Subsequent startup/control failures submitted no
VLM work; subsequent UI replays submitted no VLM work. No Qwen reasoning, FLUX, H3,
Scene 01 video or anchor-frame regeneration occurred. Both original and acceptance
copies retain exactly v1 of all three media inputs, with the user-supplied hashes.

Evidence directory: `workspace/p4b-vision-acceptance/` contains `acceptance-r1.json`,
`acceptance-r2.json`, actual requests, provider output, telemetry, control diagnostics,
`studio-review.png`, `studio-models.png`, `human-contract.json`,
`immutable-input-verification.json` and `backend-restored.json`. Historical failure
files are deliberately retained; the successful acceptance reports supersede them.
Acceptance evidence stays in this isolated workspace; 8082 returns to the original
`workspace/flux-agent-acceptance-20260907` workspace and existing provider defaults.
No `.env` values or original project history were changed. No second production owner
is running. The restored Studio worker also exercised the real 300-second idle TTL:
VLM DRAINING at 13:25:00 UTC, controlled exit code 0 at 13:25:04, then STOPPED/UNLOADED
at 13:25:05, with no OOM. The final restored backend worker is PID 23344. Consolidated
results are in `workspace/p4b-vision-acceptance/final-acceptance.json`.
P4C and independent audio work were not started.
