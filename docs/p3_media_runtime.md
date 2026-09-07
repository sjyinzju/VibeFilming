# Phase 3 — Media Runtime Foundation

Status: **media foundation ready; FLUX Direct is the first real ImageProvider; the ComfyUI video bridge is compiled and fake-HTTP verified**. No real video model is activated.

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
- `ComfyUIVideoProvider` resolves immutable frame Artifacts, uploads bytes, compiles a registered API-format workflow, observes remote execution, retrieves declared outputs, and returns video plus optional native-audio payloads. Its bundled tests use a fake HTTP service; there is no bundled H3 workflow.
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
MOVIE_AGENT_COMFYUI_ENDPOINT=http://127.0.0.1:8188
MOVIE_AGENT_COMFYUI_TIMEOUT=3600
MOVIE_AGENT_COMFYUI_WEBSOCKET_TIMEOUT=3600
MOVIE_AGENT_COMFYUI_WORKFLOW_PROFILE=minimax_h3_fl2va
```

Selecting an unregistered binding or unavailable workflow profile fails explicitly. There is no silent real-to-Mock fallback. `minimax_h3_fl2va` is reserved but intentionally unregistered in this phase.

## Progress and cancellation

Providers may report determinate progress through the job manager. ComfyUI `progress_state` values are forwarded only when the server supplies a real value/max pair; otherwise the Studio receives activity/status without a fabricated percentage. Local cancellation requested, remote cancellation dispatched, and remote interruption confirmed are separate job facts.
