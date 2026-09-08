# P4A acceptance — 2026-09-08

## P4B incremental status

Real CLI inspection of the existing Scene 01 v1 completed with `qwen3_vl` /
`movie-agent-vision`; no media was regenerated. The user explicitly authorized
temporary 8082 downtime. CLI and browser acceptance take the normal OS ownership
lock in turn, and retain the existing lease journal and checkpoint files.

| P4B measurement / invariant | Evidence |
| --- | --- |
| Cold / before startup | 117.4598 / 117.4568 GiB available |
| Model ready / before inference | 31.8074 / 31.7790 GiB available |
| Sampled minimum / after inference / after release | 31.0410 / 31.1306 / 31.1333 GiB available |
| Incremental sampled high-water | Successful CLI 86.4188 GiB; earlier cold-start/decoder-rejected attempt 87.74 GiB |
| Final configured profile | VLM resident 88 GiB, peak 92 GiB; separate 4 + 8 GiB headroom |
| Workload / basis | 24 deterministic 640px JPEG video frames plus immutable first/last image references; measured host-pressure delta, not model-file size or exact allocator peak |
| Actual inference / provider time | 55.4234 / 62.5366 seconds for CLI r1 |
| Admission | Provisional 108 GiB correctly denied before inference; calibration-only 105 GiB kept headroom; final measured 92 GiB admitted |
| Lease | Acquired durably before startup; EXECUTING before POST; successful CLI lease released |
| Lifecycle / TTL | Real automatic stop/start/readiness; restored Studio's 300-second idle TTL drained VLM at 13:25:00 UTC and stopped/unloaded it at 13:25:05, controlled exit 0, no OOM; deterministic tests verify warm reuse |
| OOM | No observed VLM OOM; deterministic OOM tests retain failure normalization, invalidation and no automatic replay |
| Uncertainty | One completed browser POST lost to acceptance telemetry error was quarantined; verified idle, controlled stop and normal reconcile released it before retry |
| Startup interruption | Startup-only uncertain leases retain inference_started=false and use normal P4A reconciliation; inference is never submitted while quarantined |
| Browser inspection | Real HTTP 32.3003 s, provider 39.4411 s; successful sampled pressure 86.5193 GiB; post-inference telemetry gaps are disclosed |
| Browser / human proof | Real inspection Artifact v2 reused for final UI/SSE/playback/refresh checks: 1 passed. Existing HumanReview/Directive accepted the real result, zero extra inference jobs |
| Restored owner | Original 8082 workspace, health HTTP 200, two projects, normal exclusive ownership, zero active leases; acceptance ports closed |

The acceptance telemetry wrapper now persists the returned response before optional
diagnostics. Resource-preparation retries retain the valid lifecycle state; tests
cover transient recovery and retry exhaustion without unintended provider calls.
Missing inference-period telemetry now produces an unknown (`null`) measured peak,
never a fabricated zero. The earlier zero observation is preserved as historical
evidence of the corrected bug and is excluded from the stated calibration.
No Qwen reasoning, FLUX or H3 inference was repeated. Detailed immutable result and
browser evidence are recorded in [P4B acceptance](p4b_vision_critic.md). FULL/targeted
resource peaks are not calibrated by this FAST workload.

Implementation and one controlled Spark inference are complete. This is evidence
for the measured workload, not a claim that arbitrary future workloads cannot OOM.

| Requirement | Result |
| --- | --- |
| 1. Contracts | Reused GenerationJob, ResourceProfile, ProviderCapabilities, ModelServiceDescriptor, ModelManager, EventBus and checkpoints. Added ModelRuntimeProfile, ResourceSnapshot, ResourceLease, SchedulerDecision and ResourceObservation. Heavy media providers declare requires_resource_lease. |
| 2. Soft pipeline | ResourceCoordinator / existing LocalJobScheduler consume eligible jobs; no model stages or second flow definition. |
| 3. Ready selection | Job dependencies, blocked/human states and continuity locks remain hard constraints. Priority precedes warm affinity. Completed jobs supply dependency evidence on resume. Adapted predecessor job IDs refer to the actual committed execution. |
| 4. Admission | Two bounds: available memory minus unmaterialized reservations must cover incremental demand plus headroom; total reserved/idle-resident envelope must fit the unified total. Lifecycle/admission are serialized. |
| 5. Unified memory | MemTotal=130669424640 bytes, about 121.70 GiB. RAM/CUDA are the same GB10 pool; CUDA diagnostics never add capacity. |
| 6. Safety | Final defaults: 4 GiB additional system reserve + 8 GiB activation/cache margin. Measured stopped-model host usage is already excluded from MemAvailable. See resource_runtime_plan.md for the retained initial-policy observation and adjustment. |
| 7. Lease | Durable reservation before startup; EXECUTING persisted before provider invocation. Missing/mismatched heavy media leases fail before transport. Shared journal and OS ownership lock prevent multiple local workers controlling the same Spark. |
| 8. Eviction | Only verified idle service, no active/uncertain lease, never BUSY/DRAINING or unknown external activity. Every stop re-samples memory. Uncertain startup is separately distinguishable from submitted inference. |
| 9. Affinity | Prefer already-warm eligible service; reload cost/freeable memory/next-ready affinity inform eviction; bounded batches protect same-priority fairness. Priority and dependencies outrank affinity. |
| 10. Lifecycle | STOPPED, STARTING, WARMING, READY, BUSY, DRAINING, STOPPING, FAILED. Model residency separately includes UNKNOWN. |
| 11. Qwen | Verified movie-agent-llm; /v1/models readiness and vLLM running/waiting metrics for idleness. Production role boundary and CLI use coordinator. |
| 12. FLUX | Verified movie-agent-flux-direct; existing /health state=ready means idle, state=busy does not. No provider-side Docker. |
| 13. ComfyUI/H3 | Verified movie-agent-comfyui; /system_stats process readiness, /queue idleness; weights stay UNKNOWN unless supported evidence exists. Verified container stop is the coarse unload fallback; hot unload is not advertised. |
| 14. OOM | Docker OOMKilled or unexpected stopped exit 137 normalize to RESOURCE_EXHAUSTED_OOM, invalidate terminated lease, mark FAILED and add visible 4 GiB failure headroom. Verified controlled stops are separately journaled by exact Docker FinishedAt; they never mask OOMKilled=true. Never automatically replay submitted work. |
| 15. Uncertain | Transport uncertainty quarantines the durable lease. Restore imports old remote evidence. Only definite termination or terminal history releases remote quarantine; a missing history alone is not proof of failure. |
| 16. Resume | Jobs/artifacts/remote evidence restore first; stopped services are started on demand. Completed jobs are reused. Resource-wait/pre-submission lifecycle failures retain job identity. |
| 17. Telemetry | Before/sampled peak/after snapshots; startup, health/warmup, execution and release timing. Last 20 reliable isolated observations form a rolling high-water estimate; append-only history retains evidence. Concurrent observations are not falsely attributed to one model. |
| 18. UI | Existing Models panel: service + residency, used/available/reserved unified memory, active job/lease and expandable recent decisions. Live browser check passed with no page errors. |
| 19. Fake tests | 31 P4A resource tests; full backend regression 256 passed / 4 explicitly opt-in integrations skipped. Frontend 38 passed and production build passed. Windows SSH fixture tests passed with process-only RemoteSigned; no real SSH/key action in those tests. |
| 20. Real acceptance | One 22-frame / 0.9167-second official-profile H3 video, effective 1024x576 after existing preflight, native audio retained. Provider execution 140.35 s. No workflow/weight/kernel changes. |
| 21. Current project | Re-read project_3effeb45f3844bf89c2c96e83770114b: completed, 21/21 nodes succeeded, all three human reviews already approved. Scene02 adaptation2 and both real H3 video artifacts were already committed. This task did not repeat planning, FLUX, Scene01 or Scene02. Existing mock post/audio boundaries remain unchanged. |
| 22. OOM recurrence | None in controlled acceptance; all three Docker OOM checks false. Initial 96 GiB H3 estimate proved low, so production budget is now 104 GiB with explicit headroom and admission tests using the real observations. |

## Actual lifecycle sequence

The acceptance harness requests warm services as setup, without Qwen/FLUX inference.
The coordinator chooses each required eviction from measured pressure:

1. Drain/stop idle ComfyUI to admit Qwen startup, then release Qwen startup lease.
2. Drain/stop idle Qwen to admit FLUX startup, then release FLUX startup lease.
3. Drain/stop idle FLUX, re-sample ~117.29 GiB available, acquire H3 lease,
   start/health-check ComfyUI, then submit one minimum-duration video request.
4. Complete H3, register artifacts and release lease. Next-ready H3 affinity keeps
   the process warm; normal idle TTL later permits automatic stop when no work remains.

Remote acceptance prompt: `62fa7502-76f2-4537-ad8e-2cf759b41267`.
Lease: `lease_a13bbbd3c01648b0b61d3e77bbb3cc12`.
Video: `workspace/p4a-resource-acceptance-20260908/artifacts/media/p4a_minimal_video/v1.mp4`.
Original report: `workspace/p4a-resource-acceptance-20260908/acceptance.json`.
UI screenshot: `workspace/_resource_runtime/resource-ui.png`.
Live API: `http://127.0.0.1:8082/runtime/resources`.

The report retains its initial 96 GiB reservation and 16 GiB policy headroom.
Observed available memory reached 13.56 GiB, corresponding to approximately
103.73 GiB isolated sampled pressure. This exceeded the original estimate without
OOM. The revised 104 GiB + 12 GiB explicit headroom fits the measured cold baseline;
readmission tests cover both cold and known-warm cases and OOM failure backoff.
No second inference was run just to re-check that arithmetic. Model service startup
and health/warmup timing are separated in the final adapter; the original acceptance
report retains its coarser combined startup measurement rather than fabricating a split.

## Operational boundaries

- One production worker controls this Spark; a second local worker fails ownership acquisition.
- Unknown residency is not credited on process restart. A verified idle stop/start
  may therefore be needed to establish a safe new baseline.
- Checkpoints do not authorize replay of a remote-uncertain job. Recovery can
  release a resource lock without granting permission for a second execution.
- Larger geometry/duration workloads still require admission; sampled telemetry
  is not an exact allocator peak. No real VLM/TTS/music integration was started.

After acceptance, the normal idle TTL stopped ComfyUI. Docker's 30-second graceful
stop elapsed and the process exited 137, **OOMKilled=false**, at
2026-09-08T03:39:54.660986383Z. The control journal shows DRAINING at 03:39:17.517039Z
and verified STOPPED at 03:39:55.140571Z. This is retained as a controlled-stop
SIGKILL diagnostic, not mislabeled as a new inference OOM. Matching is tied to
the exact container FinishedAt; a different/unexpected 137 or OOMKilled=true still
fails and feeds the OOM policy. All three services are now STOPPED with no active
lease; committed artifacts remain available.
