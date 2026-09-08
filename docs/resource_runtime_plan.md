# P4A — Resource-aware production runtime

## P4B increment

The fourth allow-listed service is `vlm` → `movie-agent-vlm`, provider `qwen3_vl`,
profile `Qwen3-VL-30B-A3B-Thinking`, served model `movie-agent-vision`, VISION kind
and modality. Existing P4A admission, eviction, TTL, warm affinity, observation and
checkpoint mechanisms apply. Initial concurrency is one with an exclusive lease.
Health uses `/v1/models`; `/metrics` running/waiting counters were verified on 8001.
Staging remains entirely in the provider data plane.

Known pre-dispatch failures are distinct from disconnected in-flight requests;
uncertain VLM requests quarantine their lease. A committed inspection Artifact can
prove completion of the same job after a crash. No blind remote replay is added.
Real Scene 01 FAST calibration sets 88 GiB resident / 92 GiB peak: successful
inspection sampled 86.42 GiB (CLI) / 86.52 GiB (browser) incremental pressure,
the larger startup sample 87.74
GiB. Keep the independent 4 GiB reserve + 8 GiB safety margin. The estimate basis
explicitly limits this evidence to 24 JPEG video frames plus two references;
FULL/targeted workloads are unbenchmarked and samples are not an allocator guarantee.
Runtime resident credit is still earned from isolated telemetry, not granted from
the configured estimate. Evidence and recovery details are in `p4b_vision_critic.md`.

## Incremental audit and plan

WorkflowGraph / ProductionGraph remain the flow source of truth. Their ready nodes,
human gates, GenerationJob dependencies, priority and continuity_chain_id are reused.
LocalJobScheduler already enforces job dependencies and chain locks; its ready-set
ordering will gain bounded warm-service affinity. No model stages are added.

Existing ResourceProfile and ProviderCapabilities remain capability contracts.
ModelServiceDescriptor gains an optional ModelRuntimeProfile and independent model
residency. ModelManager gains ensure_ready / safe drain and real Spark adapters.
ResourceCoordinator owns admission, durable leases, eviction and observations.
MediaRouter must allow a managed stopped service to be selected by capability;
health is checked after admission / lifecycle startup, before inference.
MediaRuntime and the reasoning role boundary use the same coordinator interface.
Providers continue to own transport and output only.

New contracts: ModelRuntimeProfile, ResourceSnapshot, ResourceLease,
SchedulerDecision and ResourceObservation. Existing model status event is reused;
resource snapshot/lease/decision/pressure events are added. Job remote submission
and lease IDs are optional backward-compatible fields. Runtime state is journaled
outside checkpoints so a crash after submission cannot silently free the service.
Checkpoint recovery restores jobs/artifacts/remote evidence before scheduling.

## Verified deployment (2026-09-08)

Key-only SSH: Developer, Spark spark-d1b9, existing SSH port 6081.
Container inspection confirms movie-agent-llm, movie-agent-flux-direct,
movie-agent-comfyui; these are the controller allow-list, not Domain commands.
Endpoints come from existing LLM/media settings. Docker controls use fixed verbs,
argument validation, command timeouts, idempotency and post-action verification.
ComfyUI queue must be empty before eviction; unknown remote status blocks it.
Coarse container stop is the initial verified unload mechanism. Do not claim
weights resident merely because /system_stats responds.

MemTotal=130669424640 bytes (121.7 GiB). Initially MemAvailable≈10.8 GiB,
Qwen/FLUX stopped, ComfyUI running with an empty queue. CUDA reports the same total
as RAM; nvidia-smi memory fields are N/A. Only /proc/meminfo is the admission pool;
CUDA diagnostics are retained separately, never added as independent capacity.

## Admission and soft policy

Initial policy reserved OS/runtime headroom (8 GiB) plus activation/cache safety
(8 GiB). The controlled acceptance revised the additional system reserve to 4 GiB,
retaining 8 GiB activation/cache safety; both remain configurable.
These are conservative policy budgets, not measured model sizes. Initial FLUX
estimate is grounded in the accepted ≈36.1 GiB CUDA reservation; use a 44 GiB peak
budget. Qwen/H3 estimates remain explicitly conservative until isolated telemetry
is collected. Unknown residency receives no memory credit. Account for current
available memory AND a total-memory envelope with active reservations; do not add
observed used memory to the same reservation twice. Serialize lifecycle/admission.
Leases cover startup and inference; uncertain submissions retain a quarantined
lease until remote completion/failure is independently established.

Evict only verified idle services without active/uncertain leases. Order by
near-term ready-job affinity, recoverable memory and reload cost. Re-sample after
each stop. Insufficient memory produces WAITING_RESOURCE without provider submission.
Priority and DAG eligibility outrank affinity; bound consecutive warm selections
to prevent equal-priority starvation. TTL maintenance releases idle warm services.

## Implementation and validation sequence

1. Contracts/configuration, controller/telemetry and ModelManager extension.
2. Coordinator, durable lease journal, lifecycle/OOM handling and ready ordering.
3. Wire media/reasoning, checkpoint safety and minimal Models observability.
4. Fake tests for admission, leases, lifecycle, ordering, failure and resume.
5. One minimal real H3 job using committed frames, automatic lifecycle switching,
   lease acquisition/release, telemetry and no OOM; no full-film test rerun.
6. Inspect original Scene02 prompt for project_3effeb45f3844bf89c2c96e83770114b;
   recover outputs if available, replay only after definite unrecoverable failure,
   then continue the existing workflow without repeating committed work.

Keep observed history and bounded high-water estimates; record OOM as
RESOURCE_EXHAUSTED_OOM and prevent automatic replay of submitted work.

## Real acceptance feedback

The 22-frame official-profile job used 1024x576 after existing provider preflight
adaptation, completed in 140.35 seconds, and sampled a 103.73 GiB isolated memory
increase (not an exact allocator peak). Its initial 96 GiB estimate was too low:
available memory reached 13.56 GiB and crossed the initial 16 GiB policy headroom,
without Docker OOM. This is retained as evidence, not hidden.

The production default is therefore 104 GiB for H3 plus 4 GiB additional system
reserve and 8 GiB activation/cache margin. The stopped-model baseline measured
117.29–117.60 GiB available out of 121.70 GiB total; approximately 4.1–4.4 GiB of
host usage is already excluded by MemAvailable. Adding 12 GiB explicit headroom
keeps approximately 16 GiB outside the model budget. This allows the measured H3
workload with rounded-up demand while preserving a separate activation margin.
The sampled observation and original settings remain in the acceptance report.
Larger jobs must still pass admission, and any actual OOM adds a visible 4 GiB
failure allowance that can make subsequent admission wait for operator review.

One host-wide runtime state file and OS file lock prevent independent local
workers from double-admitting the same Spark. Runtime decisions/observations also
append to an audit history; the active UI retains only a bounded recent window.
