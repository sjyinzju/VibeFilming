# P3 Extension Guide

## First real image provider

1. Implement `movie_agent.providers.media.ImageProvider`.
2. Translate `ImageGenerationRequest` inside the adapter; do not add model fields to Shot.
3. Advertise exact `ImageCapabilities` and resource profiles.
4. Return `ImageGenerationResult` plus process-local `BinaryPayload` objects.
5. Normalize transport/model failures and implement truthful status/cancel semantics.
6. Register the builder under an image binding key in `ProviderFactory` and set `MOVIE_AGENT_IMAGE_PROVIDER`.
7. Test request translation, MIME/corruption, cancellation, immutable versions, provenance, and fallback routing with a fake transport before enabling a service.

The first integration point is `ImageProvider.generate(ImageGenerationRequest)`; frame, plate, preview, job, artifact, event, and Studio code remain unchanged.

## First real video provider

Follow the same sequence for `VideoProvider.generate(VideoGenerationRequest)`. Correctly declare first/last-frame, image-to-video, references, camera control, duration, resolution, fps, cancellation, and resource limits. The adapter receives generic motion/temporal controls and owns translation to model-specific syntax.

The first integration point is `VideoProvider.generate(VideoGenerationRequest)`; strategy, prompt, job, binary transport, Range serving, QC, repair, selection, and Timeline remain unchanged.

## Optional model service

If the endpoint must be managed, implement `ModelService` separately and link it by `model_service_id` in capabilities/results. The provider must not start containers implicitly. A manager/policy decides lifecycle before provider execution.

## Acceptance checklist

- no model name or endpoint in Cinematic IR;
- no binary/base64 or local path in domain JSON;
- exact capabilities and deterministic routing;
- normalized safe errors and bounded retries;
- immutable artifact versions and complete provenance;
- safe MIME/Range browser serving;
- fake transport tests plus the unchanged Mock full pipeline.

