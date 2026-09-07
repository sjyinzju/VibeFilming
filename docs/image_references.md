# Image Reference Upload and Transport

Status: **foundation ready; no real image or video model is connected**.

## Studio entry points

| Studio location | Binding semantics | Future consumer |
| --- | --- | --- |
| Advanced → Visual → Reference Images | `project / style / visual_style` | Image and video requests across the project |
| Creative Guidance → Scene Seed | `creative_input / location / scene_concept` | Scene/shot frame planning derived from that seed |
| Creative Guidance → Key Visual | `creative_input / style / key_visual` | Image composition and shot video conditioning |
| Creative Guidance → Style Reference | `creative_input / style / visual_style` | Image and video style conditioning |
| Canonical Scene Inspector | `scene / location / scene_concept` | Shots belonging to that scene |
| Canonical Scene entities | `entity / character|location|prop` with identity/environment purpose | Shots containing that canonical entity |
| Canonical Shot Inspector | `shot / style / shot_guidance` | That shot's image and video requests |

Uploads were deliberately not added to Key Moments, audio settings, production controls, constraints, or every textarea. Key Moments are narrative commitments rather than visual assets. Draft character descriptions and location tags are primitive strings without stable entity IDs; identity binding begins once canonical `Character`, `Location`, and `Prop` IDs exist in the Scene Inspector. Composition preferences remain textual project rules; a composition image belongs in project or shot visual references.

## Upload and persistence flow

```text
browser File / drag-drop
  → multipart/form-data
  → Pillow decode + MIME/size/dimension validation
  → immutable original in BinaryArtifactStore
  → Artifact Registry metadata + user_upload provenance
  → derivative thumbnail Artifact with parent provenance
  → artifact://<artifact-id>/v<version>
  → typed MediaReference binding
```

Drafts use a client-generated opaque `draft_*` ID. Their artifacts and bindings persist on the backend while the draft payload persists in Studio local storage. `POST /projects` adopts selected draft artifacts into the project's existing stores without changing their Artifact ID or URI. Removing a reference only unbinds it; it does not delete bytes. Physical cleanup remains the responsibility of `delete_if_unreferenced` and a storage policy.

Canonical Briefs stay frozen. Post-submit additions use `POST /projects/{project_id}/image-references`; entity, scene, and shot ownership is validated against the current project, and running/cancelled projects reject mutations. Bindings are checkpointed independently from Cinematic IR.

## API

- `POST /drafts/{draft_id}/image-references`
- `GET /drafts/{draft_id}/image-references`
- `DELETE /drafts/{draft_id}/image-references/{reference_id}`
- `POST /projects/{project_id}/image-references`
- `DELETE /projects/{project_id}/image-references/{reference_id}`
- Existing `/artifacts/{artifact_id}/content|preview|thumbnail` endpoints accept `draft_id` or `project_id`.

Upload bodies contain a `file` part and a JSON `binding` form field. Responses include Artifact ID/URI/version, MIME, byte size, dimensions, creation time, SHA256, Artifact/provenance, typed reference, and preview URLs. They never expose storage paths or retain base64.

Accepted formats are PNG, JPEG, and WebP. Empty files, MIME/decoded-format mismatches, SVG, corrupt images, and files over `MOVIE_AGENT_IMAGE_UPLOAD_MAX_BYTES` are rejected. The original is never orientation-normalized or overwritten; normalization is limited to the thumbnail derivative.

## Request selection

`ReferenceResolver` selects references deterministically:

```text
Shot / Frame > Scene > relevant Entity > Creative Input > Project > legacy
```

Only entities present in the shot/scene are eligible, duplicates are removed, and the default request cap is eight. `MediaFramePlanner` receives this explicit list for `ImageGenerationRequest`; the video path passes the same relevant list plus materialized first/last frames into `VideoGenerationRequest`. Prompt compilers do not search storage or global uploads.

## Future remote transport

`MediaReferenceBinaryResolver` resolves Artifact IDs at the provider boundary. `MultipartMediaEncoder` and `ArtifactMultipartTransport` open bytes from `BinaryArtifactStore` and send:

- form field `request`: the typed request JSON;
- image `references[]` and optional `source_image` file parts;
- video `first_frame`, `last_frame`, `previous_shot`, and `references[]` file parts;
- per-part `Content-Type`, filename metadata, `X-Artifact-Id`, `X-Artifact-Version`, `X-Reference-Type`, and `X-Reference-Role`.

The transport tests use an actual HTTPX multipart encoder and FastAPI fake remote endpoint. The receiver verifies JSON, roles, MIME, filename metadata, reference type, and SHA256 equality with the uploaded original. The browser E2E runs the same proof after a real Studio upload.

Uploaded bytes are never injected into the reasoning LLM context. A future Spark Image/Video/VLM adapter must explicitly use these media contracts and transport helpers. FLUX prompt parameters, ComfyUI workflows, ControlNet, LoRA, and real inference remain out of scope.
