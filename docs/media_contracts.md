# Media Contracts

All contracts in `movie_agent.media` derive from strict, versioned Pydantic `ContractModel`. Provider-specific options are restricted to explicit extension envelopes; cinematic meaning is typed.

## Common vocabulary

- `MediaModality`, `MediaDimensions`, `MediaDuration`, `MediaEncoding`
- `MediaReference` and `ReferenceType` (character, location, prop, style, first/last/previous frame, voice, audio)
- `MediaRequestBase`, `MediaResultBase`, `MediaArtifactMetadata`
- `MediaCapabilityRequirement`, `ResourceProfile`

References contain a stable reference ID, artifact identity/version, `ReferenceBindingScope`, `ReferencePurpose`, and optional project/entity/scene/shot/creative-input binding. Display metadata may include the original basename, MIME, byte size, and dimensions. They never contain bytes or local paths.

Uploaded bindings use project, creative-input, entity, scene, shot, or frame scope. `ReferenceResolver` applies deterministic scope precedence and relevance filtering before frame or video requests are compiled. Exact duplicate bindings are idempotent; unbinding does not delete the underlying Artifact.

## Image and frame

`ImageGenerationRequest` covers text/reference generation, edit, inpaint, outpaint, variation, and upscale. `ImagePurpose` covers character/location/prop plates, style frames, storyboards, boundary frames, posters, and thumbnails. `FramePlan` contains independently executable first- and last-frame requests and an optional previous-shot frame reference.

## Video

`VideoGenerationRequest` contains duration, fps, dimensions, aspect ratio, first/last/previous references, `CameraMotionSpec`, `TemporalControl`, `StartState`, and `EndState`. No field contains model syntax. Before provider execution, `VideoGenerationPreflight` reports every request/workflow incompatibility together and distinguishes deterministic adaptations from hard unsupported intent. Delivery dimensions remain on the ProjectBrief; the effective generation canvas, stable Core-owned seed, adaptation reasons, and input Artifact facts are recorded in Job and output Artifact provenance. A workflow without a structured camera binding may accept real camera motion only when its versioned binding manifest explicitly declares `camera_motion_in_prompt` and the request's bound positive prompt contains a matching `camera` section; omission or mismatch is hard unsupported. `VideoGenerationResult` records frames, codec, dimensions, duration, provider metadata, and provenance. Its additive `native_audio_outputs` list describes normal Audio Artifacts emitted by the same generation job; older single-video results remain valid unchanged.

## Audio

Speech, music, sound effects, and video-driven Foley have separate request types. The common audio result records duration, sample rate, channels, format, loudness, provider, and provenance. `AudioCue` places every dialogue/music/SFX/Foley/ambience artifact on a Timeline.

## Vision and repair

P4B pins `target_artifact_version` and `target_sha256` before dispatch. References
also carry exact versions/hashes. The provider-internal `VisionInspectionDraft`
uses canonical field definitions and proposes only scores/issues/evidence/decision/
summary. Core validates and adjudicates it, then commits a structured Artifact in
the existing store with exact parent URIs and provenance. Decoder facts determine
legal timestamps without rewriting historical metadata. Critic configuration is
part of inspection identity, so Mock evidence never satisfies a real-provider run.

`HumanRepairDirective` preserves raw feedback, accepted/dismissed issue IDs,
preserve/change requirements, exact target identity and the existing Human Gate ID.
KEEP_CURRENT records a human override; the other dispositions feed `RepairContext`
into actual generation prompts. A checkpointed state machine inside repair_accept
allocates each new frame/video version before execution and reuses committed work
after restart. Unsupported actions and exhausted budgets pause through AGENT_ESCALATION.
Proactive feedback uses the same commands; completed-project reopening is unsupported.

`VisionInspectionRequest` accepts exactly one image or video plus references, an optional expected Shot, requirements, and inspection profiles. The result contains typed scores, `MediaIssue` records with severity/time/frame evidence, and one of `PASS`, `REPAIR`, `REGENERATE`, or `HUMAN_REVIEW`.

`MediaRepairPlan` has explicit actions and a finite retry budget. It distinguishes prompt/reference/frame/camera changes, image edits, video regeneration/extension/splitting, restoration, audio regeneration/remix, and human escalation.

## Post

`Timeline`, `TimelineClip`, `VideoTrack`, `AudioTrack`, `SubtitleTrack`, `PostProductionRequest`, and `PostProductionResult` form the provider-neutral assembly boundary.

P4C adds optional `version` and `sha256` to the existing clip/cue references for
legacy compatibility. Real post requires both. Audio cues also support source-in.
The immutable Timeline artifact is pinned in PostProductionRequest by identity,
version and SHA256; request carries delivery fps/dimensions and STANDARD/HIGH quality.

`PostRenderPlan` (`media/post.py`) is an execution projection, not an editable
timeline. It records the exact Timeline, decoder facts and exact video/audio sources,
frame/sample boundaries, source in/out, planned/actual/effective durations, actions,
encoding, loudness targets and optional Timeline subtitle cues. Canonical Shot
durations remain unchanged. CUT is the supported transition; unsupported transitions,
overlaps and audio arrangements fail explicitly. Sidecar SRT uses explicit cues or
opt-in shot-level dialogue intervals; no word-level or ASR alignment is claimed.

Final MP4, SRT and JSON manifest are independent immutable artifacts. Their public
identities never include paths. Final metadata/provenance include measured codecs,
duration, fps, sample rate, size, SHA256, QC, exact plan/hash and all source versions.
See [post/export policy and acceptance](p4c_post_export.md).
