# Layer 2 — FLUX ImageProvider integration and real acceptance

2026-09-07 (Asia/Shanghai). **Real Qwen → real FLUX → completed workflow passed.**

## Implementation and configuration

`movie_agent/providers/flux_direct.py` implements the existing ImageProvider.
ProviderFactory registers `mock`, real `flux_direct`, and unavailable `comfyui`.
Only the configured image provider enters the registry. No real-to-Mock fallback.

Set these non-secret values in `.env`, or in PowerShell before starting the backend:

```powershell
$env:MOVIE_AGENT_IMAGE_PROVIDER='flux_direct'
$env:MOVIE_AGENT_FLUX_ENDPOINT='http://127.0.0.1:9001'
$env:MOVIE_AGENT_FLUX_TIMEOUT='660'
```

The user's `.env` was not edited; existing LLM configuration was reused. Acceptance
Studio uses API 8082 / Vite 5174, workspace `workspace/flux-agent-acceptance-20260907`.
[Open accepted project](http://127.0.0.1:5174/?project=project_ba41e17ddc724deab7802abf545719fa).
Use **Open inspector → Artifacts**, or select the shot and expand a frame card.
Models shows current bindings; each Artifact records the provider actually used.

## Data path and capability boundaries

Qwen role outputs → Shot + GenericImagePromptCompiler → capability-aware FramePlanner
→ ImageGenerationRequest → MediaRouter / GenerationJob → FluxDirectImageProvider
→ multipart HTTP through SSH tunnel → Spark → PNG BinaryPayload → existing MediaRuntime
→ BinaryArtifactStore + Artifact Registry → artifact URI → existing preview endpoint.

- FIRST_FRAME and LAST_FRAME are integrated because they are existing workflow boundaries.
  No new autonomous character/location plate planner was added.
- First frame: T2I without refs, or one explicit SOURCE_IMAGE / generated previous
  continuity frame as Img2Img input. Last frame: Img2Img from the first frame.
- Uploaded first-frame source is consumed once; last frame inherits its ancestry
  through the first frame. Explicit source plus previous continuity frame is multiple
  conditioning and is rejected, not silently reduced.
- Style/character/multiple refs, masks, inpaint/outpaint, LoRA, structured edits and
  advanced guidance are unsupported. Rejections are visible media Job failures.
- Inspector provides a single first-frame source upload before approval of the shot
  gate. Extra files are rejected; Replace/Remove manages the existing source.
- Canvas fits provider bounds and rounds down to multiples of 16. Brief unchanged:
  1920×1080 becomes 1024×576 here. Requested/actual dimensions and policy are recorded.
  There is no hidden provider-level reduction in dimensions, steps or model precision.
- Existing compiler is reused. Negative text is appended as textual constraints;
  there is **no native negative conditioning**. This mapping is explicitly recorded.
- Resolver pins the selected Artifact version and sends original bytes. No local paths
  or base64 travel to Spark. PNG size/format/dimensions/hash/seed/mode are checked.
- Error responses are normalized. Timeout/busy/OOM are not automatically retried;
  remote completion can be uncertain. Cancellation prevents later local image commits
  but cannot promise remote GPU cancellation (API 1.0 has no cancellation endpoint).
- Completed storyboard checkpoints skip image generation. Immutable versions and bytes
  survive resume. Exactly-once recovery within a partially failed storyboard is future
  work; the completed-stage case was verified with a fresh engine and byte hashes.

## Real acceptance evidence

Project: `project_ba41e17ddc724deab7802abf545719fa`.
One scene, one six-second shot, all six Qwen roles committed, all 21 nodes completed.
Explicit real Agent integration test: **1 passed in 195.96 s**.
Final downstream film is still Mock, **not a real generated video**.

| Artifact | Mode | Canvas | Inference / HTTP seconds |
| --- | --- | --- | --- |
| frame_scene_01-SHOT-01_first | TEXT_TO_IMAGE | 1024×576 | 25.736 / 26.476 |
| frame_scene_01-SHOT-01_last | IMAGE_TO_IMAGE | 1024×576 | 16.071 / 17.240 |

URIs: `artifact://<above-id>/v1`. Provider `flux_direct`; model `FLUX.1-dev`;
service `flux-direct-image`; endpoint `http://127.0.0.1:9001`; seed 42, steps 28,
guidance 3.5, Img2Img strength 0.6. Last-frame parent is the first-frame artifact.
Compiler/version, prompt package, project/scene/shot, parameters, source version/hash,
timings and completion time are preserved in provenance.

- First SHA256: `fa8fb2d0d9ab81baefc49b78adce6bdf01165a5d3d1e1aafec039b1a3daab747`.
- Last SHA256: `f2d8232a344c80039170ff17f9b333aae91dd4dbe1ed056cf8dcc5f554a89bec`.
- API preview returned identical PNG hashes; fresh checkpoint resume preserved both
  URIs/versions/hashes. MockImageProvider was not registered.
- Real browser check: **1 passed**, both images visibly decoded at 1024×576, no Mock
  badges, four other modalities marked Mock, no page errors.
- Project evidence: `flux_acceptance.json`, immutable `artifacts/media/`, checkpoints,
  durable events, and `studio-flux-preview.png` under the workspace above.

## Checks and repeatability

Ordinary tests never generate real images:

```powershell
.\.venv312\Scripts\python.exe -m pytest -q
cd web
npm test
npm run build
npm run test:e2e
```

Explicit real Agent test creates a fresh project and preserves evidence:

```powershell
$env:MOVIE_AGENT_RUN_FLUX_INTEGRATION='1'
$env:MOVIE_AGENT_IMAGE_PROVIDER='flux_direct'
$env:MOVIE_AGENT_FLUX_ACCEPTANCE_WORKSPACE='E:/movie-agent/workspace/flux-agent-acceptance-20260907'
.\.venv312\Scripts\python.exe -m pytest tests/test_flux_agent_integration.py -q -s
```

Browser-only verification of an existing project, from `web/`, without generation:

```powershell
$env:MOVIE_AGENT_FLUX_PREVIEW_PROJECT='project_ba41e17ddc724deab7802abf545719fa'
npx playwright test --config playwright.flux.config.ts
```

Frontend: 33 unit tests passed; build passed; final regular browser suite 8/8 passed.
One initial 5-second approval wait timeout was followed by unchanged targeted and full
passes. Build retains the >500 kB bundle warning; no unrelated bundle refactor was made.
Final Python regression: **194 passed, 4 opt-in tests skipped (35.12 s)**.
The real FLUX integration was separately opted in and passed. Compileall and git diff
whitespace checks also passed. Qwen and FLUX remained healthy at handoff.

## Remaining media and extension point

Video, Vision/VLM/critic, Audio/TTS/music/SFX/Foley and Post remain Mock.
ComfyUIImageProvider is only an unavailable extension point at the same factory boundary.
A future adapter must declare truthful capabilities and keep ComfyUI workflow compilation
outside Domain. No video model or ComfyUI installation was performed.
