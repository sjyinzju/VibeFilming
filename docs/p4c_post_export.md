# P4C — real post, timeline conform and final film export

Status: **implemented and real acceptance passed, 2026-09-08**. No model was
downloaded or called. Work stops at P4C; P4D audio and P5 consistency/aesthetic
criticism are outside this change.

## Architecture and contracts

The existing production DAG and Timeline remain authoritative:

```text
selected immutable video + same-job native audio
  → exact immutable Timeline
  → PostRenderPlan (deterministic execution projection)
  → MediaRuntime.post_process → FFmpegPostProcessor
  → real rough_cut → deterministic full_film_review
  → FINAL_CUT_APPROVAL (exact rough-cut version/SHA)
  → final_render using the approved plan
  → immutable FINAL_FILM + Render Manifest → Studio / attachment download
```

The adapter is registered as `ffmpeg-post`. It runs on the local CPU worker with
one process-wide render slot. No Spark resource lease, model startup, heavy runtime
or remote inference is involved. Real errors never fall back to Mock.

`media/post.py` defines `ExactPostSource`, `PostSegment`, and `PostRenderPlan`.
The projection records project, deterministic plan ID/policy revision, exact Timeline
identity/version/SHA, delivery geometry/fps/aspect, encoder settings and audio policy.
Every segment records exact video/audio identity/version/hash/provider/source job,
ffprobe facts (duration, codec, video fps/frame count/geometry/pix_fmt/time base/start
time; audio sample rate/channels/start time), source ranges, planned and effective
durations, absolute timeline/frame/sample positions, gain, fades, silence and actions.
It contains no executable paths or arbitrary FFmpeg argument envelope.

TimelineClip and AudioCue gain additive optional version/hash fields for old
checkpoints. Real post requires them. Legacy Timeline migration explicitly excludes
the old Mock audio assembly and binds real native audio by source job, preserving
video order and edit ranges. A previously pinned Timeline containing Mock audio
fails validation; it is not silently filtered. Canonical Shot duration is unchanged.

## Conform policy

- Absolute Timeline boundaries round to the nearest delivery frame (half up).
  Clips must be contiguous and nonempty. Source PTS reset before conform.
- Video trims source ranges, freezes the last frame when short, and emits the exact
  planned frame count. CFR conversion uses FFmpeg fps with deterministic rounding.
- Display aspect is preserved, including source pixel aspect. Output uses ordinary
  deterministic scaling and letterbox/pillarbox as needed. No AI upscale or stretch.
- Audio is 48 kHz stereo. Source ranges reset PTS, resample, trim and pad to integer
  sample boundaries corresponding to the video. No tempo/audio stretching. Missing
  audio uses `anullsrc`, with `generated_silence=true` in the plan.
- Native audio must share the pinned video's source job. Real registered audio from
  other providers can enter through explicit cues. Overlapping multi-track mixes are
  not implemented in this release and fail explicitly. Independent Mock audio never
  enters the real renderer, even if it is an unused cue.
- Intermediate MOV retains an explicit video time base and PCM audio. MKV's
  millisecond time base failed strict CFR testing and is not used. PCM avoids adding
  AAC priming/padding at every cut; AAC is encoded once for the whole film.
- Whole-film two-pass `loudnorm`, default −16 LUFS / −1.5 dBTP / LRA 11, follows
  clip conform and concatenation. Actual input/output measurements and final decoded
  AAC loudness are retained. Silence-only films bypass impossible loudness targeting.
- **CUT only**. Non-CUT transition intent is rejected rather than ignored. The
  existing contract has no typed transition-duration semantics; no speculative
  crossfade duration or audio offset is introduced.
- SRT sidecar supports existing explicit Timeline cues. The optional re-export
  subtitle switch can create shot-level dialogue intervals using output_language.
  This is not ASR, word alignment, translation, or precise speech synchronization.
  Burn-in is not enabled. This real project had no subtitle cues, so no SRT was added.

Encoding profiles use H.264/libx264, yuv420p, AAC 192 kbps, and MP4 faststart:

| Profile | CRF | Preset |
| --- | --- | --- |
| STANDARD | 21 | medium |
| HIGH | 18 | slow |

Final QC checks readable streams, positive size/duration, exact frame count,
resolution, fps, each decoded PTS interval, codecs/pix_fmt, 48 kHz stereo, start zero,
faststart atom order, real source flags, and A/V end drift ≤ one delivery frame.
Average fps tolerance is 0.0001 fps for container-duration rounding; per-frame
interval tolerance is 10 µs. A real 23.976 fps regression proves this boundary;
24 fps acceptance is exact. Final decoded true peak must be below 0 dBTP and within
0.5 dB of the configured ceiling; integrated loudness within 1.5 LU of target.
Any failed check prevents a succeeded final job and artifact commit.

## Configuration

Defaults retain Mock for existing tests. Enable only local post as follows:

```text
MOVIE_AGENT_POST_PROVIDER=ffmpeg
MOVIE_AGENT_POST_FFMPEG=ffmpeg
MOVIE_AGENT_POST_FFPROBE=ffprobe
MOVIE_AGENT_POST_TIMEOUT=1800
MOVIE_AGENT_POST_TEMP_ROOT=
MOVIE_AGENT_POST_LOUDNESS_LUFS=-16
MOVIE_AGENT_POST_TRUE_PEAK_DB=-1.5
```

The ProjectBrief quality_level selects STANDARD/HIGH. Executables/temp root are
operator configuration, never browser parameters. Subprocesses receive argv lists
with shell disabled, trusted Artifact-resolved media paths, and controlled temp
filenames. Success/failure cleans partial work; optional diagnostics retain only
16 small path-free failure records. Real media streams from disk into immutable
storage and downloads in 1 MiB chunks.

## Persistence, approval and re-export

Plan identity fingerprints exact Timeline revision/content, selected source versions
and hashes, profile and post policy. An explicit re-export command creates a new
Timeline revision even with unchanged settings; resuming that revision reuses its
exact deterministic jobs. A committed output's hash is checked before reuse. A
missing manifest is recovered without encoding again; an old running job is
reconciled to success when its committed output is recovered. Unregistered/index
orphan bytes are removed only at the exact next-version target. Registered versions
are never overwritten.

The Final Gate subject is the actual rough-cut MP4 and exact source summary. Approval
pins rough-cut version/hash; final_render reads its plan and exact Timeline rather
than whatever media was selected later. Rejection preserves the existing held-state
semantics. Acceptance uses a clearly labelled technical approval fixture through the
real review API; it does not claim that a person approved film aesthetics.

`POST /projects/{project_id}/exports/reexport` accepts a typed body with optional
`resolution`, `fps`, `quality`, `subtitles`, `shot_order`, `audio_gain_db`, and
`use_selected_sources`. It changes only post nodes, retains old reviews/outputs and
requires a fresh rough-cut gate. Example:

```json
{"resolution":"1920x1080","quality":"high","subtitles":true}
```

`shot_order` can remove or reorder existing clips; gain/cue edits remain on Timeline.
Free-text review notes are preserved, not parsed as execution instructions. Changes
to story or generated content use the existing repair/revision mechanism. Completed
project media-feedback reopening remains unsupported; explicit post-only export is
the separate supported command.

## Download and Studio

```text
GET /projects/{project_id}/artifacts/{artifact_id}/versions/{version}/download
GET /projects/{project_id}/exports/final
GET /artifacts/{artifact_id}/preview?project_id=...&version=...
```

The first route validates project context and provenance, exact identity, binary
integrity, safe filename, MIME and size. It returns attachment Content-Disposition,
Content-Length, SHA256 ETag and X-Artifact-Id/Version/SHA256 headers. It accepts no
filesystem path. Binary media, SRT and JSON manifest/plan are downloadable.
The convenience route resolves the selected real QC-passed Final Film with a
recorded approval, then streams that exact version. It does not guess filenames.
The current app is a local single-user service; existing project-context boundaries
are enforced here. Account authentication/multi-tenant authorization remain a
separate repository concern.

Preview remains inline and supports HTTP Range/seek independently from attachments.
Studio Final Render and Artifact Inspector display the real player, geometry, fps,
duration, size, codecs, version, SHA, QC/status, audio origins and download links.
Final Gate shows the real rough-cut player and timeline source/version table. Actual
FFmpeg frame/out_time/speed events update GenerationJob → EventBus/SSE → Studio.
Progress refers to the named current pass; phases without a meaningful percentage
show activity. No simulated aggregate percentage is fabricated.

## Real acceptance evidence

Source project: `project_3effeb45f3844bf89c2c96e83770114b`. Original workspace was
hashed before and after and remained byte-for-byte unchanged. The isolated
`workspace/p4c-post-acceptance/` copy reused these inputs:

| Source | Version | SHA256 |
| --- | --- | --- |
| video_SCENE_01-SHOT-01 | 1 | 20466de1f9e2b8daaf934c41c2eb0e5b8e61d8c16c25f05fc75c8c1202227d7f |
| video_SCENE_01-SHOT-01_generated_native_audio | 1 | 94bdb17fe6db9db7dcead29c819715ad40835516bb87bf60f13d515116d726f1 |
| video_SCENE_02-SHOT-01 | 1 | ae940c2424b456d8c2fb2ef98ccb9b372b2e9af2f0a0eec3a4e8dbede82d2603 |
| video_SCENE_02-SHOT-01_generated_native_audio | 1 | d72fe09ad2827db53c8bce37ccf64e5a962f69c7cde4aa333d336b2b72cafae5 |

Native associations use the corresponding `generate:SCENE_01-SHOT-01:v1:adaptation1`
and `generate:SCENE_02-SHOT-01:v1:adaptation2` source jobs.

| Shot | Planned | ffprobe video | ffprobe audio | Effective | Timeline |
| --- | --- | --- | --- | --- | --- |
| Scene 01 | 15 s | 15.083333 s / 362 frames | 15.075 s | 15 s / 360 frames | 0–15 s |
| Scene 02 | 15 s | 15.083333 s / 362 frames | 15.075 s | 15 s / 360 frames | 15–30 s |

Both generated videos are 1024×576/24 fps; both audio inputs are AAC/32 kHz/stereo.
Post scales to 1920×1080, trims tails, and converts audio to 48 kHz without changing
playback speed. There is a hard cut at frame 360 / 15 seconds.

Final accepted policy is `p4c.2`. The exact plan is
`artifact://post_plan_4cde83fdd65d5ac9c8c66468cd3f7b82917fb4a7080997d85d1c29681ad91a56/v1`,
SHA256 `8c11140a0ef7c7b2aeca09ae0ffe968a976a6f31269177c0df13f7dc75a79912`.
It pins `artifact://timeline/v1`, SHA256
`da3d0518126a96b3f332896e6fe315abc32a96cd3daa8cd2933850c7520b6d0b`.

- Rough cut: **artifact://rough_cut/v3**, 33.047 seconds local post time.
- Final film: **artifact://final_film/v3**, ArtifactType FINAL_FILM, 35.563 seconds
  local post time, completed 2026-09-08 14:50:14 UTC.
- SHA256 of both MP4s: **ba20532fc87ded7ba9854c2491e223c1ee0bb3ee106c020652728db5a379189b**.
- **30.000 s, 720 frames, 1920×1080, 24/1 CFR, H.264/yuv420p + AAC, 48 kHz, stereo**.
- **12,417,964 bytes**. Video and audio start at 0; both end at 30.000 s;
  **A/V end drift = 0.000 s** against a 0.0416667 s limit.
- Actual decoded final audio: **−16.01 LUFS, −2.38 dBTP**, LRA 9.50 LU.
- All source/plan/final Mock flags are false. Every source's provider is
  `comfyui-video`; final provider is `ffmpeg-post`. No independent Mock audio appears.
- Manifest: **artifact://final_film_manifest_v3/v1**. Separate immutable JSON includes
  final identity/hash, exact full plan, sources, source ranges, actions, subtitles,
  QC, measurements, encoder profile and actual timestamps/version strings.
- FFmpeg and ffprobe: **8.1-essentials_build-www.gyan.dev**.

Earlier Mock v1 and initial real v2 remain immutable. The policy-recording update
produced v3 through post only. Identical MP4 hashes are expected because the edit and
encoding operations stayed the same; the plan/QC evidence became more explicit.

Actual TCP FastAPI download returned **HTTP 200**, `video/mp4`,
`attachment; filename="final_film_v3.mp4"`, Content-Length **12417964** and matching
ETag/X-Artifact-SHA256. Downloaded `final_film.mp4` was written from streamed HTTP
response bytes and its SHA matched the registry exactly. The selected-final
convenience endpoint also returned that same hash. No workspace-copy substitute was
used. `rough_cut.mp4` was downloaded independently through the same exact API.

Ten independently decoded frame comparisons cover first/middle/last frames and both
sides of the cut. Normalized mean pixel error is 0.00061–0.00139, consistent with
scaling/encoding, verifying sampled shot order and absence of inserted boundary
black frames. Source/native-audio waveform correlations are **0.9999208** and
**0.9998462** over 0.1–14.9 s of each segment. This proves native waveform inclusion
and alignment, not film aesthetics or semantic lip-sync.

The acceptance script stops/restarts actual server processes at the pending Final
Gate and after completion: rough artifact and pending review persist, final artifact
and successful jobs persist, and no completed FFmpeg work is repeated. Upstream
Qwen/FLUX/H3/VLM/audio methods are guarded to fail if called: **0 new inference calls**.

## Verification and reproduction

```powershell
.\.venv312\Scripts\python.exe -m pytest tests/test_p4c_post.py -q
.\.venv312\Scripts\python.exe -m scripts.accept_post --accept
.\.venv312\Scripts\python.exe -m scripts.verify_post_acceptance
cd web
npm test
npm run build
npm run test:e2e
npm run test:e2e:post
$env:MOVIE_AGENT_RUN_POST_INTEGRATION='1'
npm run test:e2e:post
```

The default post browser suite uses tiny locally encoded offline fixtures, with
real Core/API/FFmpeg, and forbids model inference. Existing MockPostProcessor tests
remain in the normal regression suite. FFmpeg/ffprobe must be present in CI; no large
model is needed. Real browser acceptance is opt-in and reuses committed real outputs.

Backend tests cover exact/hash pins, changed input/profile versions, fractional fps,
trim/freeze-pad, letterbox, missing audio/silence, native lineage, Mock rejection,
whole-film loudness, codec/QC, corruption/failure/timeout/cleanup, partial recovery,
manifest/subtitles, approval pins, restart/re-export, exact downloads/project errors,
Range separation and bounded streaming. Frontend tests cover rough/final players,
metadata, all download links, pass progress/activity and error state. Browser tests
verify actual playback/seek, gate, final download SHA, refresh, no Mock badge on real
film, no filesystem URLs and no upstream requests/jobs.

Evidence files: `acceptance.json`, `render_manifest.json`, `decoded-verification.json`,
`rough_cut.mp4`, `final_film.mp4`, `studio-final.png`, `browser-real.log`,
`browser-fixture.log`, `browser-regression.log`, `backend-regression.log`, and
`frontend-regression.log`, all under the isolated acceptance workspace.

Final regression results: **315 backend tests passed, 4 opt-in upstream-inference
tests skipped**; **45 frontend tests passed**; **production build passed**;
**9 existing Studio browser tests, 1 offline post browser test, 1 real post browser
test passed**. The normal `npm run test:e2e` command now includes the offline post
suite. The existing Windows PowerShell installer tests require a process-local
`PSExecutionPolicyPreference=Bypass` in this environment; no machine/user execution
policy was changed. No application test was weakened. `git diff --check` passed.
The pre-existing frontend bundle-size advisory remains (about 565 kB uncompressed).

DoD scope is satisfied for this first post release: real provider/plan/preflight,
delivery/fps/A/V/native/silence conform, Mock exclusion, rough gate/final render,
immutable artifacts/manifest/QC, Studio/progress/download, actual HTTP hash proof,
resume and post-only versions, offline and opt-in real browser coverage, regression
and documentation. Unsupported mixes/transitions fail explicitly; authentication,
burn-in, independent real audio and aesthetic full-film criticism are not claimed.
