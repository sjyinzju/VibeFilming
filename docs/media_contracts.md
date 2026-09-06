# Media Contracts

All contracts in `movie_agent.media` derive from strict, versioned Pydantic `ContractModel`. Provider-specific options are restricted to explicit extension envelopes; cinematic meaning is typed.

## Common vocabulary

- `MediaModality`, `MediaDimensions`, `MediaDuration`, `MediaEncoding`
- `MediaReference` and `ReferenceType` (character, location, prop, style, first/last/previous frame, voice, audio)
- `MediaRequestBase`, `MediaResultBase`, `MediaArtifactMetadata`
- `MediaCapabilityRequirement`, `ResourceProfile`

References contain artifact identity/version and optional entity/shot bindings. They never contain bytes or local paths.

## Image and frame

`ImageGenerationRequest` covers text/reference generation, edit, inpaint, outpaint, variation, and upscale. `ImagePurpose` covers character/location/prop plates, style frames, storyboards, boundary frames, posters, and thumbnails. `FramePlan` contains independently executable first- and last-frame requests and an optional previous-shot frame reference.

## Video

`VideoGenerationRequest` contains duration, fps, dimensions, aspect ratio, first/last/previous references, `CameraMotionSpec`, `TemporalControl`, `StartState`, and `EndState`. No field contains model syntax. `VideoGenerationResult` records frames, codec, dimensions, duration, provider metadata, and provenance.

## Audio

Speech, music, sound effects, and video-driven Foley have separate request types. The common audio result records duration, sample rate, channels, format, loudness, provider, and provenance. `AudioCue` places every dialogue/music/SFX/Foley/ambience artifact on a Timeline.

## Vision and repair

`VisionInspectionRequest` accepts exactly one image or video plus references, an optional expected Shot, requirements, and inspection profiles. The result contains typed scores, `MediaIssue` records with severity/time/frame evidence, and one of `PASS`, `REPAIR`, `REGENERATE`, or `HUMAN_REVIEW`.

`MediaRepairPlan` has explicit actions and a finite retry budget. It distinguishes prompt/reference/frame/camera changes, image edits, video regeneration/extension/splitting, restoration, audio regeneration/remix, and human escalation.

## Post

`Timeline`, `TimelineClip`, `VideoTrack`, `AudioTrack`, `SubtitleTrack`, `PostProductionRequest`, and `PostProductionResult` form the provider-neutral assembly boundary.

