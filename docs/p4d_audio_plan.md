# P4D incremental audio plan

Started 2026-09-09. Preserve the existing uncommitted P4C implementation and its
immutable acceptance workspace. No model downloads, moves, weight edits, ASR,
SFX/Foley models, or upstream Qwen/FLUX/H3/VLM inference are authorized in this phase.

## Incremental audit

- Existing `AudioProvider.generate`, `SpeechGenerationRequest`,
  `MusicGenerationRequest`, `AudioGenerationResult` and `MediaRuntime.generate_audio`
  already provide routing, jobs, binary registration and P4A leases. Reuse them.
- Character contains voice constraints; Shot narrative contains ordered dialogue
  strings; PerformanceSpec supplies character and emotion. Core must resolve exact
  committed text/speaker deterministically, and reject ambiguous speakers.
- Existing Timeline has exact version/SHA pins on AudioCue. AudioTrack lacks a label.
  P4C rejects overlapping cues and only mixes one source per video segment. Extend
  its execution projection and FFmpeg graph without replacing the renderer.
- Real post currently skips the Mock `audio_post` generation and binds native audio.
  Independent real speech/music must become ordinary ready jobs and persisted audio
  artifacts, without reopening screenplay/video/review jobs.
- Studio has reusable binary audio preview and exact download endpoints. Add typed
  voice/dialogue/music commands and inspectors on these existing boundaries.
- P4A controls only four explicitly named Docker containers. Extend its allow-list
  with `tts` and `music`; profile budgets must come from actual isolated telemetry.
- Spark read-only verification: aarch64, NVIDIA GB10, driver 580.82.09. The specified
  models exist (Base/VoiceDesign 4.3 GiB each, tokenizer 651 MiB, ACE-Step 9.4 GiB).
  Existing model containers are stopped. No audio containers exist yet.

## Implementation sequence

1. Verify official package APIs/dependencies and the installed NVIDIA image ABI.
   Build separate pinned images; mount existing models read-only, enforce offline
   model loading. Bring up loopback 8002/8003 and run isolated real smoke/telemetry.
2. Minimal contracts: versioned CharacterVoiceProfile pinned to immutable anchor;
   DialogueCue pinned to committed text and cue-level timing; neutral voice design
   request extension; named tracks and configurable mix/timing policy.
3. Real Qwen3-TTS and ACE-Step AudioProvider adapters with binary transport,
   bounded errors/timeouts and no Mock fallback. VoiceDesign once, Base clone per
   cue, exact request fingerprints and restart reuse. Persist remote music task IDs.
4. Deterministic extraction, placement, measured-duration/audio QC, bounded mild
   pace handling and explicit HUMAN_REVIEW for unfit/ambiguous dialogue. Native
   audio is production sound, never canonical speech. Compiler-only speech suppression.
5. Extend P4C exact plan for three tracks, native/music ducking, stems, authoritative
   SRT, optional burn-in, existing whole-film loudnorm and delivery/QC/download flow.
6. Audio/voice controls and previews in Studio. Voice changes invalidate dependent
   speech/post only; scene music changes invalidate post only. Existing DAG remains
   the workflow authority; no fixed model-service execution chain.
7. Offline unit/integration/browser checks; opt-in real acceptance in an isolated
   copy of the completed real project, guarded against all upstream AI calls. Reuse
   native video/audio, produce anchor/two speech cues/music and a new final version.
   Verify resume, immutable hashes, downloads, resource lifecycle and regression.

## Acceptance limits and evidence

Record cold/startup/ready/inference/after/TTL observations and sampled peaks before
setting conservative production budgets. Do not describe sampled peaks as allocator
guarantees. Deterministic QC checks playable audio, real duration/rate/channels,
silence/clipping and timing; it cannot prove transcription, stable perceived timbre
or absence of vocals. Retain explicit human listening acceptance and mark it pending
until a person supplies a result. No word-level or lip-sync claim. Report each DoD
item honestly, including any externally blocked items, and stop before P5.

## Current checkpoint

Three independent audio images are built and verified on Spark. Isolated real
VoiceDesign, Base clone (two Chinese lines and one English line), and 15-second
instrumental music passed technical checks. P4A measured all requested lifecycle
phases before profiles were set: TTS 11/16 GiB; Music 11/15 GiB resident/peak budget.
Both isolated and production TTL stops finished with exit 0 and OOMKilled=false.
Original model mounts are read-only; original checkpoint Python hashes and the
four upstream container IDs/states are unchanged. No upstream inference was run.

The guarded real acceptance command produced two character anchors/profiles,
three canonical speech cues, two scene music files, three stems, SRT and real
rough_cut v4 (SHA e839429fc4ba7ed470c9f374f22af93885176c77f64586b70642103b2607ab63).
The separate source P4C project remains unchanged. After explicit user approval,
the exact rough v4 Gate was resolved and local final export completed in 32.235 s.
final_film v4 is mock=false and byte-identical to approved rough v4, with full
technical QC and exact-version MP4/SRT/three-stem HTTP downloads verified. No new
audio or upstream inference occurred. The user reported overlapping H3/TTS voices
at the opening and an overly juvenile female voice. The listening result is
recorded as needs revision and pinned to final v4 and the three speech versions.

Full backend: 330 passed / 4 opt-in skipped; final review-version fix: 4 passed.
Frontend: prior 48 passed, final audio display check: 3 passed; browser: prior 11
passed plus real Studio playback; TypeScript and production build passed again.
See [actual evidence and 27-point report](p4d_audio.md) and its linked JSON.

Remaining: address the two listening issues in a subsequent audio revision and
obtain a new human listening verdict; native emotion/pace controls unsupported by
official Base, automatic duration-fit retry and overlapping speech. Longer
workloads remain unbenchmarked. The authorized technical export is complete; stop before P5.
