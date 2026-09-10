# P4D audio services

Status on 2026-09-09: all three images built; real TTS and instrumental Music
inference, health and P4A lifecycle calibration passed. Eight required service
files were uploaded to the authorized Spark. No repository archive, credentials,
environment files or media was uploaded. The user-approved final film v4 is now
exported. Human listening is recorded as needs revision (opening H3/TTS voice
overlap and a female voice that sounds too juvenile); see
[actual acceptance evidence](../../docs/p4d_audio.md).

The runtime uses Ubuntu 24.04 at an immutable digest, Python 3.12 and matched
official Torch/TorchAudio/TorchVision 2.10.0/2.10.0/0.25.0 CUDA 13.0 ARM64 wheels.
Ordinary pinned Python dependencies use the Tsinghua PyPI mirror following verified
PyPI download timeouts; the Torch triplet uses the official PyTorch cu130 index.
No NVIDIA/ComfyUI/vLLM site-packages are inherited. Pip constraints prevent a
transitive dependency replacing this ABI. Both services use SDPA without a
FlashAttention extension. Qwen-TTS is pinned to 0.1.1; the ACE-Step official
application source is pinned to ca1e85fe9430179831e6bc6be790c332190a3866.

Actual local image IDs (RepoDigests is empty for these local builds):

- `movie-agent-audio-runtime:p4d`: `sha256:e0d2186e9d832c0715a3ec76b20c652a0a1c08e4d9c1d6363b54e4f26bc7998d`
- `movie-agent-tts:p4d`: `sha256:ba45190f49016fb456dbe278bea1680ce440a87a1af8c50b9deda5540f9326b5`
- `movie-agent-music:p4d`: `sha256:1df7161cefefe798256098ae50de08c7f992234a11099676de5a7c07b4b2d13d`

Exact Python and transitive package versions, plus unfiltered `pip check` results,
are recorded in [acceptance JSON](../../docs/p4d_audio_acceptance.json). `pip check`
reports excluded UI/training/download/optional-LM dependencies and NVIDIA's sbsa
wheel platform label. CUDA/cuSPARSELt import and real API inference passed; this
does not certify all optional features of the upstream Python distributions.

Deployment uses `--gpus all --network host --restart no` and the following mounts:

| Container | Read-only model mount | Writable output mount | Bind |
|---|---|---|---|
| movie-agent-tts | `/home/Developer/models/audio/qwen3-tts:/models/qwen3-tts:ro` | dedicated TTS output directory → `/output` | 127.0.0.1:8002 |
| movie-agent-music | `/home/Developer/models/audio/ace-step-1.5:/opt/ace-step/checkpoints:ro` | dedicated music output directory → `/output` | 127.0.0.1:8003 |

Mount music's `.cache` to its own writable persistent directory as well, preserving
official task results across restarts. Never mount an existing service environment.
HF offline flags are inherited. The music launcher replaces only the API's download
hook with a local existence check. The official handler normally synchronizes its
Python implementation into checkpoint folders, which fails against the RO mount.
The launcher copies only Python files into `/output/runtime-checkpoints-v2` and
symlinks all weights, JSON and tokenizer files back to the RO mount. Official
source synchronization can then write the overlay; original model implementation,
metadata and weights stay unchanged. Original checkpoint Python hashes were
verified after real inference. Missing checkpoints fail instead of downloading.

TTS: GET `/health`, GET `/v1/models`, JSON POST `/v1/voices/design`, form POST
`/v1/audio/speech` (JSON metadata field `request`). The provider sends an exact SHA
reference to an anchor already generated and retained on Spark, so no real media
asset is uploaded. The service verifies its digest and exact reference transcript.
The optional multipart `reference` field remains for compatibility but is not used
by this deployment or acceptance.
Both generation endpoints return binary `audio/wav`. No Base64 audio JSON.
Successful generated WAVs are persisted by exact content fingerprint. Base clone
uses the exact anchor and transcript; native emotion/pace control is unavailable
in the official Base API and is disclosed in the service health/Core provenance.

Music: official `/health`, `/v1/models`, `/v1/model_inventory`, `/release_task`, `/query_result`,
`/v1/audio`, `/v1/stats`. Initial instrumental generation disables the optional
5Hz language model (`thinking=false`), with no change to ACE-Step DiT/VAE synthesis.
Existing 1.7B LM remains available on disk but is not loaded for this smoke profile.
P4A readiness uses `/v1/model_inventory` to verify the requested DiT is loaded;
the official `/v1/models` compatibility list alone does not prove loading.

P4A maps only `tts` and `music` to the two explicit container names. Measured
resident/peak budgets are 11/16 GiB and 11/15 GiB respectively; see the report for
all cold/startup/ready/inference/after/TTL measurements and estimate basis.
Both successful isolated and production runs ended in normal TTL stop (exit 0,
OOMKilled=false). A stopped service is started by the next P4A audio lease; these
ports are not kept resident merely for continuous health polling.

Sources: [Qwen official dependencies](https://github.com/QwenLM/Qwen3-TTS/blob/main/pyproject.toml),
[Qwen official clone API](https://github.com/QwenLM/Qwen3-TTS/blob/main/qwen_tts/inference/qwen3_tts_model.py),
[ACE-Step official dependencies](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/pyproject.toml),
[ACE-Step official API](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/docs/en/API.md).
