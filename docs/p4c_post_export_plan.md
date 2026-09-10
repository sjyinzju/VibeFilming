# P4C incremental audit and implementation plan

Audit scope: existing Timeline/PostProductionRequest, PostProcessor, MediaRuntime,
artifact stores, preview/range endpoints, post DAG nodes, selected media, delivery
settings and Studio inspectors. No planning/model/workflow redesign.

Verified gaps: rough_cut currently commits Timeline JSON; audio_post generates
Mock tracks; final post is Mock; FinalCut review filters only timeline artifacts.
Binary store already streams writes and range reads, uses immutable versions and
safe identities. Artifact selection is persisted. The existing native audio relation
is source_job_id; the Timeline currently omits exact versions/hashes.

Implementation sequence:
1. Extend existing Timeline source references with optional exact version/hash
   (legacy checkpoints remain readable). Pin them before committing a real timeline.
   Add PostRenderPlan as an execution projection, never an editable domain timeline.
2. Local FFmpegPostProcessor with ffprobe, frame-boundary conform, native audio or
   silence, aspect-preserving scale/pad, CFR, whole-film two-pass loudnorm and QC.
   Persist plan, subtitles and manifest; pin inputs, stream output, emit real progress.
3. Integrate existing post nodes and exact real rough-cut Final Gate; retain Mock
   default for tests. Real failure cannot fall back to Mock or acquire GPU leases.
4. Exact-version attachment downloads plus selected/approved final convenience API;
   Studio rough/final player, source summary, metadata, export links and progress.
5. Targeted tests, isolated existing-project acceptance (zero upstream inference),
   real HTTP download/hash and browser acceptance, full regression and documentation.

Initial policy: one sequential video track; CUT only (unsupported transition intent
fails explicitly); quantize absolute edit boundaries to delivery frames, reset source
PTS, trim/freeze-pad video, resample/trim/silence-pad audio to matching sample counts.
48 kHz stereo, H.264/yuv420p/AAC/faststart. A/V end drift <= one delivery frame.
Subtitles are optional sidecar from existing explicit Timeline cues, no ASR claims.
Config supports STANDARD/HIGH and whole-film loudness/true-peak targets. Diagnostics
are bounded and path-free. Re-export invalidates post nodes only and requires a new
rough-cut approval. Acceptance copies project state/media into an isolated workspace;
original completed production state remains unchanged.
