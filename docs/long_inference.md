# Long-running structured inference — P2B repair

## Evidence (2026-09-06, UTC)

The current Studio project is `project_532cb7aae9074c6b9d6887dcabdbb2a8`.
No Tunnel/Spark fault is inferred. Changes began only after its active run ended:
the final scene exhausted structured repairs with a continuity `ROLE_OUTPUT_INVALID`,
not a timeout. That separate failure and all checkpoints remain untouched.

Director's original request started 04:22:26.668, ended 04:25:26.715:
context/prompt 13,881 chars; schema 7,969 chars; input estimate 5,725 tokens;
output cap 8,192; read timeout 180s; latency 180.019s;
outcome `remote_completion_uncertain`, validation not run.
Prompt/completion usage and finish reason are **unavailable**, not zero: the client
never received the completed response. No per-request remote completion record was
available in the local ledger. Explicit Resume at 04:43:50.201 succeeded in 127.579s,
with 3,695 prompt / 5,397 completion tokens, `stop`, and passing validation.

Historical committed artifact provenance was read from `workspace/p2a-real`,
`workspace/p2a-A`, `workspace/p2a-baseline`, and `workspace/studio`; fake/mock providers
were excluded and attempts deduplicated by request ID. A committed RoleResult can
include failed validation attempts, so these measure transport completion, not only
successful contracts. Event metrics also retain the uncommitted 288.402s length stop.

| Role | Artifact attempts | Max / nearest-rank p95 latency | Max completion tokens |
|---|---:|---:|---:|
| Creative Producer | 4 | 47.477s | 2,048 |
| Story Architect | 2 | 17.726s | 760 |
| Screenwriter | 2 | 50.788s | 2,169 |
| Visual Director | 2 | 12.645s | 530 |
| Director | 2 | 162.165s | 6,844 |
| Cinematographer | 10 | 287.201s | 12,000 |

These small samples do **not** establish a stable population p95. The current second
scene additionally returned 12,000 tokens in 288.402s with `finish_reason=length`.
That is output truncation, not lost connectivity, and increasing time alone cannot fix it.

## Policy and budget rationale

At the observed 41–43 tokens/s, 8,192 tokens need about 200s; 12,000 need about 293s.
Using the slower rate, 50% headroom and about 30s setup allowance yields approximately
330s and 469s. Round to bounded role profiles, not one globally enlarged timeout:

| Role | Legacy/nonstream read profile | Streaming inactivity | Total streaming budget |
|---|---:|---:|---:|
| Creative Producer | default 180s | not enabled | unchanged normal transport |
| Story Architect | default 180s | not enabled | unchanged normal transport |
| Visual Director | default 180s | not enabled | unchanged normal transport |
| Screenwriter | 360s | 60s | 360s |
| Director | 360s | 60s | 360s |
| Cinematographer | retained 360s | 60s | 480s |

Connect/write/pool remain 10/30/10s. An independently configurable `total_timeout`
cancels local waiting only; it does not claim to cancel the remote model. Default
nonstream calls also have a safety wall-clock cap of read + 50s for connection/write/pool.
No provider timeout or malformed/incomplete stream triggers a transport fallback or
automatic repeat submission. Explicit Resume retains the existing uncertain retry flag.
For an endpoint requiring nonstream operation, set `stream=False`; to use the entire
480s cinematic budget without token activity, set its read timeout to 480s explicitly.

Output budgets are conservative ceilings, not contract edits. Screenwriter scales by
target duration, max shots and desired characters (4,096–8,192); Director by actual
screenplay scene/entity membership (up to 8,192); Cinematographer by the existing
per-scene shot budget (single-shot baseline 7,000, increasing up to 12,000). Verbatim
ledger size reserves additional space. Large plans keep original ceilings. Compactness
instructions forbid dropping required fields, scenes, or verbatim commitments. No
Scene/Shot/continuity schemas or semantic checks are weakened. These heuristics should
be reviewed against future real length-stop metrics, not treated as guaranteed sizing.

## Verified streaming transport

[vLLM's structured output example](https://docs.vllm.ai/en/stable/examples/features/structured_outputs/)
supports streaming, and [HTTPX read timeout](https://www.python-httpx.org/advanced/timeouts/)
measures waiting for network data rather than total response duration. The adapter now
uses `stream=true` with the **same** JSON Schema response format. It accumulates only
content deltas, ignores reasoning, requires finish reason plus `[DONE]`, and collects
usage. Content buffer and total duration are bounded. EOF/error/timeout before completion
returns uncertainty without any partial content crossing into RoleRunner.

Only after complete collection does the existing Pydantic → semantic → continuity →
commit → checkpoint path run. The three heavy roles were enabled after deterministic
transport tests and an opt-in **real endpoint** Creative Producer probe passed. This
probe confirmed constrained streaming, complete typed validation, finish reason and
usage, without modifying or retrying the current production. It is not a long-load benchmark.

```powershell
$env:MOVIE_AGENT_RUN_STREAMING_INTEGRATION = '1'
.\.venv312\Scripts\python.exe -m pytest tests/test_streaming_inference.py -q
```

## User-facing state

While a local provider invocation remains pending the node stays RUNNING; the Studio
shows that it is waiting for a longer structured plan. This is not a claim about GPU
activity that a nonstream client cannot observe. Unknown completion is presented as a
waiting timeout, with explanation that the remote model may still be computing and an
explicit re-execute/Resume action. Old snapshots with `ProviderFailure` also resolve
the typed uncertainty from the failed node's RoleResult. Connection failures retain
reconnect; validation failures and ordinary command conflicts do not use that label.

No P3 work, serving configuration, frozen IR or retry budget changes are included.

Full-suite verification also exposed an existing review-history clock-tie defect:
Windows can timestamp a later artifact and a review identically. Studio now uses
durable artifact-created / review-requested event ordering for the as-of boundary,
falling back to timestamps only for legacy data without that journal relation.
The equal-timestamp case is regression-tested; no historical files are rewritten.

Final checks: 107 backend tests passed (3 real-endpoint tests opt-in/skipped in the
default suite); 26 frontend tests and 7 isolated browser scenarios passed; production
build passed with the pre-existing chunk-size warning. Streaming's real-endpoint probe
was separately enabled and passed. The existing project's canonical Project, role
outputs and reviews hash identically before/after the local Studio reload. Its failed
third scene was not resumed. No full heavy-role production was re-inferred for acceptance.
