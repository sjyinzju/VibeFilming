# Media Artifacts and Browser Transport

## Identity and bytes

`Artifact.artifact_id` is stable across immutable versions. `Artifact.uri` for P3 media is `artifact://<artifact_id>/v<version>`. Only `LocalBinaryArtifactStore` maps that URI to a local file below the project workspace.

The binary store validates artifact IDs, extensions, MIME types, size limits, root containment, and non-overwrite. It supports `put`, `open`, `exists`, `size`, and `delete_if_unreferenced`. Artifact selection only changes manifest selection state; it never overwrites bytes.

## Provenance

Media provenance records project/scene/shot, strategy, prompt compiler ID/version, provider, future model-service ID, references/input artifacts, seed, parameters, parent, repair plans, and evaluations. Mock output uses this exact structure.

## HTTP surface

```text
GET  /artifacts/{id}/content
GET  /artifacts/{id}/preview
GET  /artifacts/{id}/thumbnail
POST /artifacts/{id}/select?version=N
```

Requests resolve an artifact ID through the project registry. No endpoint accepts a filesystem path. Responses set an authoritative media MIME type, `nosniff`, immutable caching, and byte-range support. Invalid or multiple ranges return 416. Video seeking therefore works without exposing a local path.

Images preview directly. Videos use their binary as preview and a deterministic poster artifact as thumbnail. Audio exposes playable WAV plus lightweight waveform metadata. No transcoding framework is installed.

