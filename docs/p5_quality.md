# P5 — Film quality and consistency

P5 is an opt-in extension of the existing production DAG, ArtifactStore, MediaRuntime,
P4A resource coordinator, audio and FFmpeg post pipeline. Existing projects are not migrated.
The isolated entrypoint is `python -m scripts.run_p5 --run-real`.

## Evidence and acceptance

`ShotQualityReport`, `SceneQualityReport` and `FilmQualityReport` aggregate existing
versioned Evaluation and VisionInspectionResult records. Video reports match artifact ID,
version and SHA; previous video reports remain immutable when a repair creates a successor.
SHOWCASE requires visual scores >= .80, cinematic >= .75, passing technical QC and
no unresolved major/critical issue. Film targets are >=75 accepted seconds and >=8 shots;
the planning target is 105 seconds, 3 scenes and 12 purposeful shots.

Unknown dimensions stay unknown. TTS presence is supported by actual speech artifacts;
intelligibility, speaker likeness and native speech overlap still require listening.
There is no ASR, WER, lip-sync or invented human approval.

Qwen3-VL observes sampled images/video. Static images have one frame and no timeline;
time ranges and motion claims are invalid. Qwen reasoning evaluates cinematic intent
from those observations and canonical context, cites evidence IDs, and proposes bounded
actions without mutating the timeline. The film proxy contains exact timeline, scene
boundaries, bounded shot summaries and sampling metadata, not exhaustive film perception.
The deployed vLLM rejects JSON Schema `propertyNames`; cinematic dimensions therefore
use explicit typed fields. The installed validator confirmed the old schema unsupported
and the replacement supported. Completed local validation failures are distinct from
uncertain remote inference.
Real long-form planning also exposed equal Vector3 values being treated as different
because their serialization version strings differed. Physical continuity now compares
coordinates and coordinate space; original schema metadata is preserved. Saved rejected
plans can be deterministically revalidated without spending another model call.
Scene-local character updates may restate existing ambient held-prop relationships;
they cannot add or remove them. Visual prompt compilation uses cached Qwen English
instructions for the image/video models while keeping canonical Chinese story and TTS intact.

## Identity and frame gate

ReferenceIdentitySet stores selected character, location, prop and style roles with exact
artifact version/hash and passing baseline inspection ID. Resolver order is character,
location, relevant props, bounded style; missing mandatory identities fail closed and
oversized reference sets require shot replanning instead of truncation.

Kontext consumes one source image only. Other selected refs inform canonical prompts and
VLM comparison; they are not falsely represented as simultaneous model conditioning.
First and last frame inspections precede H3. A failed gate blocks only that shot; expensive
video calls cannot bypass reviewed frame version/hash bindings. Minor imperfections do not
become blocking solely because they exist. A temporal drift found in video does not erase
the passing frame history.

## Existing model integration

The downloaded `flux1-kontext-dev-nvfp4.safetensors` is a Comfy NVFP4 single-file checkpoint,
not a Diffusers directory. P5 uses the installed stock Comfy loader/kernels plus the existing
FLUX CLIP-L, T5 and AE weights. Independent `movie-agent-kontext` service uses port9002;
P4A owns startup, health, busy/idle, lease, memory admission and TTL eviction.

Only `image_edit` is advertised. Multi-reference, masks, inpaint, outpaint, text-to-image
and generic image-to-image are not supported by this adapter. Requests pin source SHA,
version, instruction, preserve/change constraints, seed and output SHA. Input/config receipts
prevent reuse across changed requests. Transport uncertainty is not automatically replayed.
No model, image or package download was used for deployment.

Measured GB10 smoke at 1024x576, 28 steps: startup9.296s, inference88.447s,
sampled unified pressure30.161GiB, post-inference24.522GiB, noOOM. Configured resident25GiB,
peak35GiB includes rounded peak +4GiB; P4A separately reserves12GiB system/safety headroom.
This is sampled unified pressure, not exact CUDA allocator peak. Native Torch2.14 nv26.08
reports CUDA13.4. The official lifecycle TTL check passed.

Actual output preserved the arch/people silhouettes while warming illumination. Automated
before/after VLM validation failed (first temporal evidence, then contradictory PASS with
major issue). This is recorded as unresolved inspection, not a passing identity evaluation.
Smoke artifacts and telemetry live in `workspace/p5-kontext-smoke-03`.

## Bounded work and repair

Reservations are persisted before dispatch in immutable `quality_compute_ledger` versions.
Completed artifacts are recovered; uncertain reservations cannot silently dispatch again.
H3 <=18 overall and <=2/shot; Kontext <=2/image chain; VLM <=3 inspection revisions per
artifact ID across image versions (a deliberately conservative run policy); TTS
<=2/cue+voice revision; music <=2/scene. One inspection revision can contain up to two
bounded semantic-validation attempts. The ledger survives resume. Exhaustion is checked
before acquiring a GPU lease; changing a version does not reset this inspection budget.

Relative repair work units distinguish post, TTS, music, frame edit, frame regeneration and
video regeneration. They are policy estimates, not monetary costs or measured latency.
Unsupported automatic repairs become human review. Visual regeneration is not a remedy
for a voice or mix-only problem. Ella uses a mature adult lower-register VoiceDesign revision.
H3 prompts request ambience/foley/nonverbal sound and no intelligible generated speech.

## Studio and delivery

The existing inspector gains a Quality tab with exact previews, identity packs, quality
filters, blocking issues and version comparisons. No separate workspace model was created.
`technical_candidate_final` uses only exact accepted footage and passing real FFmpeg QC.
It leaves FINAL_CUT_APPROVAL pending and marks `human_aesthetically_approved=false`.
The normal `final_film` path still requires exact human approval.

Run records, a Morning Review page, exact downloads/stems/SRT/render manifest and acceptance
metrics are written under `workspace/p5-hero-film`. Actual acceptance results and limitations
belong in `p5_hero_film_acceptance.md`; implementing the mechanism alone does not meet film DoD.
## Overnight reference-review diagnosis (2026-09-10 local)

Initial single-subject reference checks incorrectly included world-wide character
and location requirements. They now inspect the exact intended plate and distinguish
establishing a named reference from comparing against an existing identity.
Reference labels include their entity ID and exact artifact version for later
multi-reference comparisons. Failed results remain immutable and budgeted.

The deployed Qwen3-VL Thinking service lacked a reasoning parser. The existing
NVIDIA26.08 image contains `Qwen3ParserReasoningAdapter`; a CPU-only probe confirmed
it without model loading or network access. The original stopped container is
retained for rollback. The replacement adds `--reasoning-parser qwen3`, using the
same image and read-only models. P5 now requires separate reasoning to be present
but stores only its presence, never its raw text. Parser expectation, token limit,
temperature and seed enter the critic cache fingerprint. P5 uses temperature0.6
and seed1234; the default for other projects remains temperature0.

Sources: [vLLM reasoning plus structured outputs](https://docs.vllm.ai/en/latest/features/structured_outputs/)
and [Qwen3-VL official Thinking evaluation settings](https://github.com/QwenLM/Qwen3-VL#evaluation-reproduction).
The final bounded review returned separate reasoning. Ella's unchanged image passed
all three initial-reference profiles at1.0; the earlier invented printed-name requirement
disappeared. This is an improvement on that control image, not proof that the critic is
generally calibrated. Other reviews still duplicated defects, used excessive severity
for a glow-intensity mismatch, or failed evidence validation. These remain human review.

## Actual run outcome

The Hero Film did not meet DoD:105s planned, zero accepted seconds, no H3 dispatch and
no final MP4. Only one of six identity subjects passed. Four real Kontext outputs changed
pixels, but the requested hood lowering and removal of people/flying discs did not
succeed. The earlier lighting smoke therefore establishes narrow editing capability;
it does not establish reliable identity repair or object removal.

The run stopped at the reference inspection budget (18 revisions across six subjects).
Per-reference critic validation failures no longer abort independent reference work.
Audio post with no accepted footage creates an explicit technical human gate instead
of raising an opaque empty-timeline error. Three real TTS lines and three real scene
music tracks are available for listening; no mix or listening acceptance is claimed.
All idle model services were stopped through P4A, with zero active leases.
See the actual acceptance report for metrics and the unexercised long-form paths.
