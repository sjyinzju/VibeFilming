# Media Providers, Capabilities, and Routing

## Interfaces

`ImageProvider.generate`, `VideoProvider.generate`, `VisionProvider.inspect`, `AudioProvider.generate`, and `PostProcessor.process` consume their strong request types. Health, status, and cancellation are common lifecycle methods.

Binary output crosses the in-process adapter boundary as a non-serializable `BinaryPayload`. `MediaRuntime` immediately stores it and registers the immutable artifact. Bytes/base64 never enter JSON, events, checkpoints, or Cinematic IR.

Binary reference input takes the reverse path through `MediaReferenceBinaryResolver`, `MultipartMediaEncoder`, and `ArtifactMultipartTransport`. These shared helpers resolve only Artifact IDs/versions and serialize typed image/video requests plus binary parts. Real adapters therefore do not parse filesystem paths or duplicate multipart logic.

The multipart contract uses a `request` JSON field, image `references`/`source_image` parts, and video `first_frame`/`last_frame`/`previous_shot`/`references` parts. Part headers preserve Artifact identity, version, reference type, and semantic role. Fake-remote tests verify each payload by SHA256.

## Registry and factory

`ProviderRegistry` owns callable adapters. `CapabilityRegistry` owns their current advertised capability snapshots. `ProviderFactory` maps a modality plus configured binding key to a builder. Image bindings include `mock`, real `flux_direct`, and unavailable `comfyui` (extension point only).

Registering a real binding is additive:

```python
factory.register(MediaModality.IMAGE, "my-image-service", MyImageProvider)
```

The workflow does not change.

The [FLUX adapter](flux_agent_acceptance.md) reuses the Artifact resolver and explicitly maps the standalone service's `request_json` / `source_image` multipart contract; it does not send the generic encoder's `request` / `references` format. [Service acceptance](flux_direct_acceptance.md) is recorded separately. ComfyUI has no backend implementation.

## Capability routing

`ProviderCapabilities` has typed image/video/vision/audio sections. Limits and flags include dimensions, duration, fps, reference inputs, editing, boundary frames, camera control, temporal reasoning, TTS, voice clone, music, Foley, SFX, ambience, and audio edit. Resource profiles declare logical class, memory expectation, concurrency, and exclusivity.

`MediaRouter` filters on modality, task, required capabilities, quality profile, resource class, health, then selects deterministically by provider ID. The selection records the complete capability snapshot and reason.

## Errors

Adapters normalize errors to timeout, unavailable, model-not-ready, resource-exhausted, invalid-request, unsupported-capability, generation-failed, cancelled, corrupt-media, or internal. Raw CUDA/HTTP/SDK exception text is not forwarded to workflow consumers.
