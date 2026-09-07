"""Lazy GPU dependencies and a single set of shared FLUX components."""

from __future__ import annotations

import gc
import importlib.metadata
import io
import os
import time
from pathlib import Path
from threading import Event

from PIL import Image

from .contracts import MODEL_PATH, ErrorCode, GenerateRequest, ServiceError


class FluxBackend:
    def __init__(self, placement: str = "cuda"):
        if placement not in {"cuda", "model_cpu_offload"}:
            raise ValueError("FLUX_PLACEMENT must be cuda or model_cpu_offload")
        self.placement = placement
        self.t2i = self.i2i = self.active = None
        self.info: dict = {}

    def load(self) -> dict:
        try:
            return self._load()
        except Exception:
            # A failed startup must not keep partially loaded weights reachable.
            self.t2i = self.i2i = self.active = None
            gc.collect()
            if hasattr(self, "torch"):
                self.torch.cuda.empty_cache()
            raise

    def _load(self) -> dict:
        # No Hub fallback, credential lookup, quantization, or per-request loading.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        import torch
        from diffusers import FluxImg2ImgPipeline, FluxPipeline

        self.torch = torch
        if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
            raise ServiceError(ErrorCode.MODEL_NOT_READY, "CUDA with BF16 support is required", 503)
        # GB10 CUDA allocations are not fully accounted by Docker's RAM cgroup limit.
        # Bound this allocator too, retaining at least 8 GiB for the host/other workloads.
        free, total = torch.cuda.mem_get_info()
        cuda_budget = min(40 * 1024**3, free - 8 * 1024**3)
        if cuda_budget <= 0:
            raise ServiceError(ErrorCode.RESOURCE_EXHAUSTED, "Insufficient free memory for safe model loading", 503)
        torch.cuda.set_per_process_memory_fraction(cuda_budget / total)
        if not (Path(MODEL_PATH) / "model_index.json").is_file():
            raise ServiceError(ErrorCode.MODEL_NOT_READY, "A complete local Diffusers model directory is required", 503)
        self.t2i = FluxPipeline.from_pretrained(
            MODEL_PATH, torch_dtype=torch.bfloat16, local_files_only=True,
            use_safetensors=True,
        )
        if self.placement == "cuda":
            self.t2i.to("cuda")
        # from_pipe defaults to float32 and otherwise upcasts the shared BF16 weights.
        self.i2i = FluxImg2ImgPipeline.from_pipe(self.t2i, torch_dtype=torch.bfloat16)
        # Fail loudly if a future library change breaks the no-duplicate-weights contract.
        shared = ("transformer", "vae", "text_encoder", "text_encoder_2")
        if not all(getattr(self.t2i, key) is getattr(self.i2i, key) for key in shared):
            raise RuntimeError("FLUX pipelines must share model components")
        self.t2i.set_progress_bar_config(disable=True)
        self.i2i.set_progress_bar_config(disable=True)
        self._activate(self.t2i)
        torch.cuda.synchronize()
        self.info = {
            "device": torch.cuda.get_device_name(0),
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "diffusers": importlib.metadata.version("diffusers"),
            "transformers": importlib.metadata.version("transformers"),
            "placement": self.placement,
            "cuda_allocator_budget_bytes": cuda_budget,
            "shared_components": list(shared),
            "resources_after_load": self.resources(),
        }
        return self.info

    def _activate(self, pipeline) -> None:
        if self.active is pipeline:
            return
        if self.placement == "model_cpu_offload":
            # Shared models share hooks. Rebind in each pipeline's execution order.
            if self.active is not None:
                self.active.remove_all_hooks()
            pipeline.enable_model_cpu_offload()
        self.active = pipeline

    def resources(self) -> dict:
        torch = self.torch
        result = {
            "cuda_allocated_bytes": torch.cuda.memory_allocated(),
            "cuda_reserved_bytes": torch.cuda.memory_reserved(),
            "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
            "cuda_peak_reserved_bytes": torch.cuda.max_memory_reserved(),
        }
        # GB10 unified memory is not equivalent to discrete VRAM; expose raw counters.
        try:
            free, total = torch.cuda.mem_get_info()
            result.update(cuda_free_bytes=free, cuda_total_bytes=total)
        except RuntimeError:
            pass
        try:
            import psutil
            memory = psutil.virtual_memory()
            result.update(process_rss_bytes=psutil.Process().memory_info().rss,
                          system_available_bytes=memory.available, system_total_bytes=memory.total)
        except (ImportError, OSError):
            pass
        return result

    def generate(self, request: GenerateRequest, source: Image.Image | None,
                 cancelled: Event, deadline: float) -> tuple[bytes, dict]:
        torch = self.torch

        def check_deadline(*args):
            if cancelled.is_set() or time.monotonic() >= deadline:
                raise ServiceError(ErrorCode.TIMEOUT, "Generation deadline exceeded", 504)
            return args[-1] if args else None

        check_deadline()
        pipeline = self.i2i if request.mode == "IMAGE_TO_IMAGE" else self.t2i
        self._activate(pipeline)
        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        options = dict(
            prompt=request.prompt, width=request.width, height=request.height,
            num_inference_steps=request.steps, guidance_scale=request.guidance_scale,
            generator=torch.Generator("cpu").manual_seed(request.seed),
            max_sequence_length=512, output_type="pil",
            callback_on_step_end=check_deadline,
        )
        if source is not None:
            options.update(image=source, strength=request.strength)
        with torch.inference_mode():
            output = pipeline(**options).images[0]
        torch.cuda.synchronize()
        inference_seconds = time.perf_counter() - started
        check_deadline()
        if output.size != (request.width, request.height):
            raise RuntimeError("FLUX returned unexpected dimensions")
        buffer = io.BytesIO()
        output.save(buffer, format="PNG")
        return buffer.getvalue(), {
            "inference_seconds": round(inference_seconds, 3),
            "resources": self.resources(),
        }

    def normalize_error(self, exc: Exception) -> ServiceError:
        if isinstance(exc, ServiceError):
            return exc
        torch = getattr(self, "torch", None)
        if isinstance(exc, MemoryError) or (torch is not None and isinstance(exc, torch.cuda.OutOfMemoryError)):
            gc.collect()
            if torch is not None:
                torch.cuda.empty_cache()
            return ServiceError(ErrorCode.RESOURCE_EXHAUSTED, "Insufficient memory for this operation", 503)
        if isinstance(exc, TimeoutError):
            return ServiceError(ErrorCode.TIMEOUT, "Generation deadline exceeded", 504)
        return ServiceError(ErrorCode.GENERATION_FAILED, "Image generation failed; inspect service logs using request_id", 500)
