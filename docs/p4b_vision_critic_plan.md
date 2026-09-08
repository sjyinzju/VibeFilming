# P4B — Real vision critic and human guided repair

Incremental audit (2026-09-08): `VisionInspectionRequest` addresses only an ID;
`MediaRuntime.inspect` appends a result without registering an Artifact;
`_generate_versions` recompiles the same Shot without repair input; `_repair_accept`
performs one retry and raises generic errors. Existing ArtifactStore, P4A leases,
HumanGateManager, durable events, Studio snapshots and SSE will be reused.
The DAG remains unchanged. No planning/model installation or H3 acceptance rerun.

## Implementation sequence

1. Pin target/reference versions and hashes; add a provider-internal Pydantic draft,
   deterministic semantic validation/adjudication and immutable inspection evidence.
2. Implement narrow content-addressed Spark media staging; probe the deployed vLLM
   video, sampling, mixed-media, structured-output and reasoning capabilities using
   the existing Scene 01 video. Observe cold/ready/inspection memory with P4A.
3. Register `qwen3_vl` and the allow-listed `vlm` service. Require a lease, normalize
   failures, quarantine uncertain requests, retain mock-only default CI.
4. Compile MediaRepairPlan and typed HumanRepairDirective into RepairContext.
   Run bounded repair/re-inspection within repair_accept; persist every transition
   and reuse completed exact-version work after restart. Unsupported actions pause.
5. Extend existing Human Gate commands and Studio Inspector with four dispositions,
   raw feedback, issue selection, exact-version previews, scores and lifecycle.
   Support safe feedback before Final Gate with scoped downstream invalidation;
   completed-project reopening stays unsupported unless an existing safe path fits.
6. Run deterministic provider/repair/human/resume/resource tests, real opt-in VLM
   acceptance and browser/SSE persistence checks, then full backend/frontend/build.
   Update contracts, runtime acceptance and capability documentation with evidence.

## Acceptance constraints

VLM proposes; Core validates and commits. No silent mock fallback, invented
timestamps, canonical story mutation, vendor parameters in Shot, arbitrary remote
shell, browser-to-8001 access, duplicate inference on resume, or history deletion.
Real capabilities and resource estimates must be measured, not inferred from model
size. A real PASS is valid; repair acceptance uses deterministic fake providers.

## Checkpoint

P4B completed. Real CLI and browser-triggered Qwen3-VL inspection produced immutable
Artifacts v1/v2 from the unchanged Scene 01 video v1. Measured FAST startup/inference
pressure calibrated VLM to 88 GiB resident / 92 GiB peak plus existing headroom.
The real browser UI/SSE/playback/refresh and real-result human-contract checks passed;
deterministic repair, all four human choices and proactive PASS feedback are covered.
Full backend/frontend/build and browser regressions passed. The user authorized the
temporary service interruption; the original 8082 backend was restored with normal
exclusive P4A ownership and no active lease. No ownership/state/checkpoint deletion,
repeated Qwen reasoning/FLUX/H3 inference, new media generation or P4C work occurred.
See `p4b_vision_critic.md` for actual results, recovery history and measurement limits.
