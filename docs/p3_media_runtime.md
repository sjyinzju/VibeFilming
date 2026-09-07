# Phase 3 — Media Runtime Foundation

Status: **foundation ready; FLUX Direct is the first real ImageProvider**. Other media remains Mock. No ComfyUI or video model integration.

Layer 1 adds a standalone [FLUX Direct Image Service](../services/flux_image/README.md)
outside this Agent runtime. Layer 2 connects it through the existing image runtime.
See [service acceptance](flux_direct_acceptance.md) and [Agent acceptance/configuration](flux_agent_acceptance.md).

## Runtime path

```text
Shot / Cinematic IR
  → GenerationStrategyPlanner
  → Image/Video/Audio request
  → Generic prompt compiler
  → MediaRouter + capability snapshot
  → typed provider
  → GenerationJob
  → LocalBinaryArtifactStore
  → artifact:// immutable version
  → Preview / VLM inspection
  → finite repair / selection
  → typed Timeline / PostProcessor
```

The reasoning roles, `Shot`, continuity validation, workflow graph, and role runtime do not know a model name or endpoint. A provider translates at the boundary. `ModelService` separately describes whether a future remote service is running.

## What runs now

- `MockImageProvider` returns a small valid PNG for first/last frames.
- `MockVideoProvider` returns a small valid H.264 MP4 fixture.
- `MockVisionProvider` returns structured scores, issues, evidence, and decisions.
- `MockAudioProvider` returns valid WAV fixtures for speech, music, SFX, Foley, ambience, and mix.
- `MockPostProcessor` returns a valid MP4 fixture from a typed Timeline.
- Every output follows the same Provider → Job → Artifact → Event path intended for real adapters.

These are deterministic test assets, not AI-generated media. Studio marks them `MOCK` while allowing browser playback.

User image references now enter this path through validated multipart upload, immutable Artifact registration, typed binding, deterministic request selection, and the shared provider transport layer. See [image_references.md](image_references.md). The upload foundation does not change any Mock provider into real inference.

## Persistence and recovery

Artifact metadata remains the source of truth in `LocalArtifactStore`. Binary bytes live under `artifacts/media` behind `LocalBinaryArtifactStore`; contracts persist only `artifact://<id>/v<n>`. Checkpoints include jobs, artifacts, inspections, media repair plans, and the Timeline. Interrupted media states normalize to queued on resume.

## Configuration

The five bindings default to `mock`:

```text
MOVIE_AGENT_IMAGE_PROVIDER=mock
MOVIE_AGENT_VIDEO_PROVIDER=mock
MOVIE_AGENT_VISION_PROVIDER=mock
MOVIE_AGENT_AUDIO_PROVIDER=mock
MOVIE_AGENT_POST_PROVIDER=mock
MOVIE_AGENT_IMAGE_UPLOAD_MAX_BYTES=20971520
```

Selecting an unregistered binding fails explicitly. There is no silent real-to-Mock fallback.

## Progress and cancellation

Providers may report determinate progress through the job manager. Without a real progress signal the Studio displays activity/status only; it never synthesizes percentages. Local cancellation prevents later Core commits. Provider cancellation is best effort and does not claim that remote computation stopped.
