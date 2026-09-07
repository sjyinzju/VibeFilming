# ComfyUI Video Integration Plan

Status: implementation plan for the model-neutral ComfyUI bridge. This phase does not
install, launch, or run MiniMax H3 and does not invent an H3 node graph.

## Audited boundaries

Movie Agent already routes `Shot` through `GenerationStrategyPlanner`, compiles a
provider-neutral `PromptPackage`, records the selected strategy on `GenerationJob`, and
creates a strict `VideoGenerationRequest`. Artifact input bytes are resolved only at the
provider boundary through `MediaReferenceBinaryResolver`; provider output bytes are
immediately persisted by `LocalBinaryArtifactStore` and registered as immutable
`Artifact` versions by `MediaRuntime`.

The Spark checkout at ComfyUI commit `fbed745c8d7d62573b099cd61fe51cb64b9b807e`
was inspected read-only. Its local API uses `/prompt`, `/ws`, `/history/{prompt_id}`,
`/view`, `/upload/image`, `/object_info`, and `/system_stats`. It also provides
`/api/jobs/{job_id}` and idempotent `/api/jobs/{job_id}/cancel`; the legacy `/queue` and
`/interrupt` endpoints remain present. WebSocket execution events include
`execution_start`, `executing`, `progress_state`, `execution_success`,
`execution_error`, and `execution_interrupted`.

## Field ownership

| Existing Movie Agent data | ComfyUI compiler use | Ownership |
| --- | --- | --- |
| `prompt_package.positive_prompt` / `negative_prompt` | Semantic prompt slots | Binding manifest |
| `seed` | Optional seed slot | Binding manifest |
| `width`, `height`, `fps`, `duration_seconds` | Typed scalar slots; frame count may be derived deterministically | Binding manifest |
| `first_frame`, `last_frame`, `previous_shot`, selected references | Resolve immutable Artifact version, hash bytes, upload, then bind returned ComfyUI input identifier | Asset bridge + binding manifest |
| `camera_motion`, `temporal_control`, `start_state`, `end_state` | Bind only when the selected workflow explicitly declares support | Template capability + binding manifest |
| `provider_parameters` | Only explicitly declared parameter slots; unknown values fail | Binding manifest |
| `mode` and planned generation strategy | Select and validate a workflow profile | Provider/profile registry |
| `required_capabilities`, quality and resource class | Routing and validation only | Movie Agent runtime |
| request/job/project/scene/shot/output IDs and timestamps | Never node inputs | Movie Agent domain/runtime |
| Artifact IDs, versions, hashes and provenance | Upload/output lineage; never filesystem paths | Artifact bridge/runtime metadata |
| endpoint, timeouts, ComfyUI runtime version and remote prompt ID | Never workflow inputs | Provider/runtime metadata |

## Adapter design

`ComfyUIWorkflowTemplate` is a frozen, versioned record containing an API-format,
node-ID-keyed workflow, a verified canonical SHA-256 hash, modality and generation mode,
supported/required capabilities, output declarations, and optional display-only node or
edge metadata. A registry rejects replacement of an existing template/version.

`WorkflowBindingManifest` is separately versioned. Each binding declares a semantic
slot, node ID, input name, expected value type, required flag, transform, and optional
expected class type. Static validation rejects missing nodes/inputs, class mismatches,
missing required slots, output-node errors, and duplicate or conflicting bindings.
Optional live validation can compare declared classes and inputs with `/object_info`.

`ComfyUIWorkflowCompiler` accepts a request, the already-planned strategy, effective
provider capabilities, resolved/uploaded input handles, and a registered template plus
manifest. It deep-copies the template and produces a deterministic
`ComfyUIExecutionSpec` containing canonical API prompt JSON, input mappings, expected
outputs, template ID/version/hash, binding ID/version, model profile, and normalized
execution metadata. Unsupported capabilities or undeclared provider parameters fail
before transport.

`ComfyUIClient` owns all HTTP/WebSocket details and normalized errors. The video
provider owns profile selection and capability intersection, while `MediaRuntime` keeps
job/event and Artifact registration responsibilities.

## Artifact and lifecycle flow

Input: `artifact://id/vN` -> selected immutable Artifact -> binary store bytes -> SHA-256
-> multipart `/upload/image` with a deterministic remote basename -> ComfyUI input
identifier -> semantic binding. Windows and Spark filesystem paths never cross the
boundary.

Output: history output declaration -> `/view` bytes -> `BinaryPayload` -> binary store
-> immutable video/audio Artifacts with shared source job, provider/model/template
provenance, prompt ID, timings, and input Artifact IDs/versions/hashes. The existing
video result stays backward compatible and gains optional typed native-audio outputs;
the Timeline already accepts normal audio Artifacts through `AudioCue`.

Remote queue/running/progress/success/failure/interruption is recorded as provider
activity on the existing media job/event path. Local cancel requested and remote cancel
dispatched/confirmed remain distinct.

## Delivery slices and verification

1. Golden compiler slice: fixed API-format fixture, template/binding validation, exact
   canonical JSON, optional/required fields, first/last frames, and fail-fast cases.
2. Client slice: health, object info, upload, prompt submission, event wait/history,
   output retrieval, job status, and cancellation against a real HTTP fake.
3. Provider/runtime slice: request -> uploaded immutable inputs -> compiled prompt ->
   fake execution -> readable MP4 plus WAV -> two registered Artifacts with shared
   lineage and preserved input hashes.
4. Registry/config, strategy/capability intersection, unavailable/unsupported errors,
   targeted regression, then one full test run.

`minimax_h3_fl2va` is reserved as a profile ID only. Activating it requires an official
API-exported H3 FL2VA workflow and its verified binding manifest in the next phase.
