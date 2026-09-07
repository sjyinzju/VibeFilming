# Layer 1 — FLUX Direct Service real acceptance

Date: 2026-09-07 (Asia/Shanghai). **Passed on the real Spark GPU, not Mock.**

## Deployment actually verified

- Key-only SSH works; actual hostname spark-d1b9, user Developer.
- Base nvcr.io/nvidia/pytorch:26.08-py3, digest
  sha256:3becd068f49bd2ad38f90db5f9a4803019a76933a24e63d821376c44e7a9200a.
- Service image movie-agent-flux-direct:layer1, image ID
  sha256:df5ea8df5ed7fc8f20695adc3ae224bc944dd44803c370432af9adab7e1a75da.
- NVIDIA GB10; host driver 580.82.09; verified container CUDA 13.4 forward compatibility.
  NVIDIA Torch 2.14.0a0+4fdf77b940.nv26.08 retained unchanged. Diffusers 0.37.0,
  Transformers 4.56.2, Accelerate 1.10.1, Safetensors 0.6.2.
- Read-only local model /home/Developer/models/image/flux-dev; no weights downloaded.
- Container movie-agent-flux-direct, one worker, loopback 127.0.0.1:9001.
  Local SSH tunnel 127.0.0.1:9001; no public listener or ComfyUI.
- Full BF16 CUDA, **no offload/quantization**. Load 225.757 s, count 1.
  Four shared component identity checks passed; allocated after load 33,755,798,016 B.
- CUDA allocator limited to min(40 GiB, initial free minus 8 GiB); container RAM 52 GiB.
  Existing movie-agent-llm was never stopped or reconfigured.

## Real HTTP results

Committed examples: 1024×1024, 28 steps, seed 42, guidance 3.5.
Img2Img consumes T2I PNG bytes with strength 0.6.

| Measurement | T2I | Img2Img |
| --- | --- | --- |
| HTTP / MIME | 200 / image/png | 200 / image/png |
| PNG bytes | 964,770 | 979,355 |
| Inference seconds | 44.887 | 26.894 |
| Service generation seconds | 45.185 | 27.211 |
| HTTP wall seconds | 47.531 | 32.100 |
| Peak CUDA allocated bytes | 36,347,384,832 | 36,347,385,344 |
| Peak CUDA reserved bytes | 38,761,660,416 | 38,761,660,416 |
| Process RSS bytes | 1,948,024,832 | 1,991,032,832 |
| System available bytes | 23,193,698,304 | 23,142,297,600 |

- T2I SHA256: 4a0c575de913bf3f4793088feee6d693311668c01375e7adf3af6670bbfa0031.
- Img2Img SHA256: b037312caa32cad66625920acbb5e181c5b36388d89e5a66d6bd17776c22fac9.
- Both PNGs opened and visually inspected: blue ceramic teapot on a wooden table.
  Img2Img changes details while preserving the scene; not a claim of structured editing.
- INPAINT probe: HTTP 422 UNSUPPORTED_CAPABILITY.
- After both requests: health ready, load count still 1.
- Raw PNGs/headers/capabilities/health: workspace/flux-layer1-acceptance-20260907/.

## Failures reproduced and corrected before acceptance

1. NVIDIA Torch module version differs from wheel metadata. Installer now pins the
   distribution version and verifies both versions in a fresh interpreter.
2. Diffusers 0.35.2 triggers an uninitialized logger when newer TorchAO lacks legacy
   tensor classes. 0.37.0 includes the fix; build now checks both actual pipeline imports.
   [Upstream source](https://github.com/huggingface/diffusers/blob/v0.37.0/src/diffusers/quantizers/torchao/torchao_quantizer.py).
3. from_pipe defaults to FP32: the original wrapper upcast shared BF16 weights,
   causing OOM (~59 GiB allocation) and temporary host memory pressure. Explicit
   torch_dtype=torch.bfloat16 fixes it, guarded by a failing-then-passing regression.
   [Upstream implementation](https://github.com/huggingface/diffusers/blob/v0.37.0/src/diffusers/pipelines/pipeline_utils.py).
   Docker RAM limits alone did not constrain GB10 CUDA allocation; a CUDA allocator
   budget is now applied too. Failed container stopped; host memory recovered.

Stopped diagnostic containers retained: movie-agent-flux-direct-import-failed and
movie-agent-flux-direct-fp32-failed. Deployment sources/cache are in the dedicated
/home/Developer/movie-agent-flux-layer1.wQF4N1 directory. No other workload was removed.

Standalone CPU tests: 39 passed. [Agent acceptance](flux_agent_acceptance.md).
[Deployment and HTTP runbook](../services/flux_image/README.md).
