# FLUX Direct Image Service — Layer 1

Standalone service, **not an Agent ImageProvider**. No imports from `movie_agent`,
no changes to the production workflow, no model download. Actual deployment and
acceptance status is recorded in [the acceptance record](../../docs/flux_direct_acceptance.md).

## Architecture

`multipart HTTP → strict contract / bounded upload → one admission slot → one GPU
thread → shared FluxPipeline / FluxImg2ImgPipeline → raw PNG + measurement headers`.

- `contracts.py`: standalone version 1.0 wire schema, capability truth table, safe errors.
- `app.py`: background one-time startup, health, request validation, serialized worker,
  deadline handling, input decoding, response validation and metrics.
- `backend.py`: lazy Torch/Diffusers imports, local-only BF16 loading, inference,
  shared component identity checks, optional evidence-driven CPU offload.
- `__main__.py`: fixed **127.0.0.1:9001**, **one Uvicorn worker**, no reload.
- `Dockerfile` / `install.py`: derive from the node's verified NVIDIA PyTorch image,
  install service dependencies in a venv while retaining that exact Torch build.

The model is fixed at `/home/Developer/models/image/flux-dev`. It must be a complete
Diffusers directory with `model_index.json`, transformer/VAE, both text encoders,
tokenizers, scheduler, configs and safetensors. A single raw checkpoint is not enough.
Missing files fail startup; offline mode never fetches missing weights.

## Deployment on Spark

First inspect `docker ps`, `docker images`, `free -h`, `nvidia-smi` and model files.
Do not stop another workload, upgrade drivers, replace the verified base image, or
consume its memory without checking available resources. GB10 uses unified memory;
system memory availability matters as well as CUDA allocator counters.

Copy **only this service directory** to a dedicated directory on Spark. From the
repository root (or equivalent copied layout), substitute the **verified image
tag/digest**, not an assumed latest version:

```bash
docker build \
  --build-arg NVIDIA_PYTORCH_IMAGE='<verified NVIDIA PyTorch image tag or digest>' \
  -t movie-agent-flux-direct:layer1 \
  -f services/flux_image/Dockerfile services/flux_image

docker run -d --name movie-agent-flux-direct \
  --gpus all --network host --shm-size=2g \
  --mount type=bind,source=/home/Developer/models/image/flux-dev,target=/home/Developer/models/image/flux-dev,readonly \
  movie-agent-flux-direct:layer1
docker logs -f movie-agent-flux-direct
```

Host networking is intentional: the process binds the **host loopback**. Do not
add `-p`, bind `0.0.0.0`, or use the public custom-service port. Verify on Spark:

```bash
ss -ltnp 'sport = :9001'
curl -i http://127.0.0.1:9001/health
```

Alternatively, inside a dedicated verified NVIDIA container with this code mounted:

```bash
python -m venv --system-site-packages /opt/flux-venv
source /opt/flux-venv/bin/activate
python services/flux_image/install.py
python -m services.flux_image
```

The top-level Movie Agent `pyproject.toml` does **not** acquire Torch/Diffusers.
Inference library versions are pinned in this directory. NVIDIA's existing pip
constraints are respected; a conflict should fail installation and be investigated,
not bypassed with a silent Torch replacement. Record the final package versions
and base image digest after a successful build.

### Memory and deadlines

Default loading is `FluxPipeline.from_pretrained(..., torch_dtype=torch.bfloat16,
local_files_only=True, use_safetensors=True)` followed by `.to("cuda")`.
`FluxImg2ImgPipeline.from_pipe(..., torch_dtype=torch.bfloat16)` shares transformer, VAE and both encoders; identity
checks and serialized inference protect the shared state. No second weight load.
Explicit dtype is essential: the default `from_pipe` dtype is FP32 and upcasts the
shared weights. Startup also limits the CUDA allocator to the smaller of 40 GiB or
initial free CUDA memory minus 8 GiB. Docker's RAM limit alone did not constrain CUDA
allocations on this GB10. This is a per-process allocator guard, not a reservation
against other processes growing after startup. Partially loaded pipelines are released
on startup failure; the failed container can be stopped to reclaim its CUDA context.

Only after a recorded CUDA/memory failure, consider restarting with
`-e FLUX_PLACEMENT=model_cpu_offload`. Offload hooks are rebound when switching
pipelines because their component execution orders differ. **No automatic fallback,
quantization, reduced dimensions/steps, or request retries.** First assess other
active workloads; do not stop them without permission.

The verified 26.08 base includes a newer TorchAO: Diffusers 0.35.2 fails during import
before loading this non-quantized model. The service pins 0.37.0 (upstream logger
initialization fix), and `install.py` verifies both pipeline imports at build time.
An optional `wheels/` directory in the build context enables offline installation
(`--no-index --find-links`); populate it with matching Python 3.12 Linux ARM64 wheels,
not Windows wheels. Keep the cache outside Git and include all missing dependencies.

`FLUX_GENERATION_TIMEOUT_SECONDS=600` bounds HTTP generation time. On timeout, the
worker receives a cooperative cancellation flag checked at denoising step boundaries.
It retains the admission slot until computation exits; further generations get 429.
CUDA calls/text encoding/VAE decode cannot be forcibly interrupted by a Python
thread. A stuck driver call may require an explicit operator container restart;
the service never pretends the GPU stopped or starts overlapping work.

## HTTP API

`GET /health`: 200 when `ready`/`busy`, 503 during startup or after failure. Returns
state, actual load count/time, runtime versions/device/placement, resource snapshot
after loading, and a sanitized startup error code. An accessible HTTP process is
not necessarily a loaded model. `GET /capabilities` returns implementation capabilities
and the full request JSON Schema even while the model loads. Read health separately.
OpenAPI: `GET /openapi.json` (interactive documentation at `/docs`).

`POST /v1/images/generate` **always** uses `multipart/form-data`:

| Part | Contract |
| --- | --- |
| `request_json` | Exactly one UTF-8 JSON text part, at most 16 KiB; not a file part |
| `source_image` | Required only for IMAGE_TO_IMAGE; one PNG/JPEG/WebP, at most 20 MiB and 16,777,216 pixels |
| `reference_files[]` | Reserved; supplying even one part returns UNSUPPORTED_CAPABILITY |

No path/URL/base64 inputs. Duplicates, unknown JSON fields, unknown multipart fields,
MIME/content mismatch, corrupt/animated images and oversized bodies are rejected.
Body size is enforced as it streams, including requests without Content-Length.
Source preprocessing applies EXIF orientation, converts to RGB, then resizes to the
requested dimensions without cropping (aspect ratio may change).

Request fields: `mode` is exactly `TEXT_TO_IMAGE` or `IMAGE_TO_IMAGE`; nonblank `prompt`
(up to 4000 characters); `width` / `height` 256–1024, multiples of 16 (default 1024);
`steps` 1–50 (default 28); `guidance_scale` 0–20 (default 3.5); `seed` 0–2^63−1
(default 42). Img2Img requires `strength` in (0,1] and `floor(steps * strength) >= 1`.
Strength is invalid for T2I. Img2Img runs a strength-dependent subset of the steps.
Same seed is repeatable within a stable stack, not a promise of cross-device bit identity.

Success: **200 `image/png`**, one raw image (no JSON/base64 wrapper). Content-Length
is the actual PNG byte count. Headers include `X-Request-Id`, `X-Flux-Mode`,
`X-Flux-Seed`, `X-Flux-Width`, `X-Flux-Height`, `X-Flux-SHA256`,
`X-Flux-Model-Load-Seconds`, `X-Flux-Inference-Seconds`, `X-Flux-Generation-Seconds`,
and `X-Flux-Resources` (compact JSON). Inference timing covers pipeline invocation
and CUDA synchronization; generation timing additionally covers source decode/resize,
output encoding and verification. HTTP wall time additionally includes upload/network.

Errors: `{"error":{"code":"...","message":"safe message","request_id":"..."}}`.

| Code | HTTP / meaning |
| --- | --- |
| INVALID_REQUEST | 422, or 400 for malformed HTTP/multipart, 413 for size limits |
| MODEL_NOT_READY | 503, loading or failed startup |
| UNSUPPORTED_MODE | 422, unknown mode |
| UNSUPPORTED_CAPABILITY | 422, known but unsupported mode/conditioning/option |
| GENERATION_FAILED | 500, sanitized internal generation failure |
| RESOURCE_EXHAUSTED | 429 when busy; 503 on allocation failure |
| TIMEOUT | 504, deadline exceeded |

Tracebacks stay in service logs. Completion logs contain request ID, mode, seed,
steps, dimensions, bytes, hash, timings and resource counters, **not prompts or images**.
This is a trusted loopback service, without public authentication/rate-limit infrastructure.

## Explicit real HTTP acceptance (not run by pytest)

On Windows, establish the user-owned tunnel in a separate terminal (use your
configured SSH identity; no credentials belong in a script or repository):

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:9001:127.0.0.1:9001 -p <ssh-port> <user>@<spark-host>
```

Once `/health` is 200 and ready, execute from the repository root. Use a fresh output
directory so prior evidence is not overwritten. These actual requests are the smoke
test; there is no separate GPU test program or automatic generation in CI.

```powershell
New-Item -ItemType Directory -Path workspace/flux-layer1-acceptance
curl.exe --fail-with-body --max-time 30 http://127.0.0.1:9001/health
curl.exe --fail-with-body --max-time 30 http://127.0.0.1:9001/capabilities
curl.exe --fail-with-body --max-time 660 -D workspace/flux-layer1-acceptance/t2i.headers -o workspace/flux-layer1-acceptance/t2i.png -F 'request_json=<services/flux_image/examples/t2i.json' http://127.0.0.1:9001/v1/images/generate
curl.exe --fail-with-body --max-time 660 -D workspace/flux-layer1-acceptance/i2i.headers -o workspace/flux-layer1-acceptance/i2i.png -F 'request_json=<services/flux_image/examples/i2i.json' -F 'source_image=@workspace/flux-layer1-acceptance/t2i.png;type=image/png' http://127.0.0.1:9001/v1/images/generate
curl.exe --fail-with-body --max-time 30 http://127.0.0.1:9001/health
```

Check exit codes, HTTP status, exact MIME, PNG decoding and 1024×1024 dimensions,
hash/byte count and visual content. A .png filename alone is not evidence; curl can
save an error JSON. Capture first load time, both inference times, headers, logs,
`nvidia-smi`, `free -h` and CPU/GPU counters. Confirm load count remains one across
both modes. Submit an unsupported capability and confirm explicit rejection as well.

CPU regression checks (ordinary suite; no Torch import needed):

```powershell
.\.venv312\Scripts\python.exe -m pytest tests/test_direct_image_service.py -q
```

## Future integration and ComfyUI boundary

Layer 2 now supplies an [Agent ImageProvider](../../docs/flux_agent_acceptance.md) that translates the existing
`ImageGenerationRequest` / `MediaReference` and resolves Artifact bytes into this
service's `request_json` / `source_image`. The existing generic Artifact transport
uses different multipart names (`request`, `references`); it is **not** wire-compatible
as-is. A future adapter must map fields, reject unsupported reference semantics,
parse PNG/headers and register output artifacts. That adapter lives in
`movie_agent/providers/flux_direct.py`, not in this standalone service.

ComfyUI can later live in a separate sibling service/backend with its own capability
contract and lifecycle, alongside this Direct service. Workflow compilation belongs
inside that future adapter/service, never in Shot/domain or this FLUX request schema.
No ComfyUI install, API call, workflow JSON or provider exists in this Layer 1 work.

Primary references used for the implementation:
[Diffusers FLUX pipelines](https://huggingface.co/docs/diffusers/api/pipelines/flux),
[shared-pipeline loading and offload caveats](https://huggingface.co/docs/diffusers/using-diffusers/loading),
[NVIDIA framework support matrix](https://docs.nvidia.com/deeplearning/frameworks/support-matrix/).
