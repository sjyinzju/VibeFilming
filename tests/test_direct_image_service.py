"""CPU-only HTTP/worker tests. Never import torch or generate with a real model."""

from __future__ import annotations

import asyncio
import io
import json
import threading
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from services.flux_image.app import create_app
from services.flux_image.backend import FluxBackend
from services.flux_image.contracts import MAX_BODY_BYTES, ErrorCode, ServiceError, parse_request


def request_json(**changes):
    return json.dumps({"mode": "TEXT_TO_IMAGE", "prompt": "A ceramic teapot on a wooden table",
                       "width": 1024, "height": 1024, "steps": 28, "guidance_scale": 3.5,
                       "seed": 42, **changes})


def png(size=(32, 32)):
    output = io.BytesIO()
    Image.new("RGB", size, "navy").save(output, format="PNG")
    return output.getvalue()


class FakeBackend:
    normalize_error = FluxBackend.normalize_error

    def __init__(self):
        self.loads = 0
        self.calls = []
        self.release = threading.Event()
        self.release.set()
        self.entered = threading.Event()
        self.failure = None

    def load(self):
        self.loads += 1
        return {"device": "CPU TEST DOUBLE", "shared_components": ["transformer", "vae"]}

    def generate(self, request, source, cancelled, deadline):
        self.entered.set()
        self.release.wait(5)
        self.calls.append((request, source.size if source else None))
        if self.failure:
            raise self.failure
        return png((request.width, request.height)), {"inference_seconds": 0.01, "resources": {}}


async def ready(app):
    await app.state.image_service.load_task


def post(client, raw=None, **kwargs):
    return client.post("/v1/images/generate", files={"request_json": (None, raw or request_json()), **kwargs})


def test_http_png_modes_shared_load_and_capabilities():
    async def scenario():
        backend = FakeBackend()
        app = create_app(backend)
        async with app.router.lifespan_context(app):
            await ready(app)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                health = (await client.get("/health")).json()
                assert health["ready"] and health["model_load_count"] == 1
                caps = (await client.get("/capabilities")).json()
                assert caps["image_to_image"] and caps["text_to_image"]
                for key in ["inpaint", "outpaint", "multi_reference", "structured_edit", "controlnet", "lora", "kontext", "fill"]:
                    assert caps[key] is False
                assert caps["request_schema"]["additionalProperties"] is False
                t2i = await post(client)
                i2i = await post(client, raw=json.dumps({**json.loads(request_json()), "mode": "IMAGE_TO_IMAGE", "strength": 0.6}),
                                 source_image=("source.png", png(), "image/png"))
                for result in (t2i, i2i):
                    assert result.status_code == 200, result.text[:100] if result.status_code != 200 else ""
                    assert result.headers["content-type"] == "image/png"
                    assert result.headers["x-flux-seed"] == "42"
                    assert len(result.headers["x-flux-sha256"]) == 64
                    assert Image.open(io.BytesIO(result.content)).size == (1024, 1024)
                assert backend.loads == 1 and len(backend.calls) == 2
                assert backend.calls[0][1] is None and backend.calls[1][1] == (1024, 1024)
                assert (await client.get("/health")).json()["model_load_count"] == 1
                assert "multipart/form-data" in (await client.get("/openapi.json")).json()["paths"]["/v1/images/generate"]["post"]["requestBody"]["content"]
    asyncio.run(scenario())


@pytest.mark.parametrize("changes,code", [
    ({"mode": "UNKNOWN"}, "UNSUPPORTED_MODE"),
    *[({"mode": mode}, "UNSUPPORTED_CAPABILITY") for mode in ["INPAINT", "OUTPAINT", "MULTI_REFERENCE", "KONTEXT", "FILL"]],
    *[({key: value}, "UNSUPPORTED_CAPABILITY") for key, value in [("lora", []), ("controlnet", {}), ("workflow_json", {}), ("references", []), ("negative_prompt", "x")]],
    ({"unexpected": "x"}, "INVALID_REQUEST"),
    ({"prompt": " "}, "INVALID_REQUEST"),
    ({"width": 1023}, "INVALID_REQUEST"),
    ({"width": "1024"}, "INVALID_REQUEST"),
    ({"seed": True}, "INVALID_REQUEST"),
    ({"seed": -1}, "INVALID_REQUEST"),
    ({"guidance_scale": float("nan")}, "INVALID_REQUEST"),
    ({"steps": 0}, "INVALID_REQUEST"),
    ({"strength": 0.6}, "INVALID_REQUEST"),
    ({"mode": "IMAGE_TO_IMAGE"}, "INVALID_REQUEST"),
    ({"mode": "IMAGE_TO_IMAGE", "strength": 0.01}, "INVALID_REQUEST"),
])
def test_schema_rejects_unsupported_or_invalid(changes, code):
    raw = json.dumps({**json.loads(request_json()), **changes})
    with pytest.raises(ServiceError) as error:
        parse_request(raw)
    assert error.value.code == code


@pytest.mark.parametrize("raw", ["{", "[]", '{"mode":"TEXT_TO_IMAGE","mode":"IMAGE_TO_IMAGE"}', "null"])
def test_malformed_or_ambiguous_json(raw):
    with pytest.raises(ServiceError) as error:
        parse_request(raw)
    assert error.value.code == "INVALID_REQUEST"


def test_http_validation_never_silently_ignores_parts():
    async def scenario():
        backend = FakeBackend()
        app = create_app(backend)
        async with app.router.lifespan_context(app):
            await ready(app)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                cases = [
                    (await post(client, **{"reference_files[]": ("ref.png", png(), "image/png")}), "UNSUPPORTED_CAPABILITY"),
                    (await post(client, mask_image=("mask.png", png(), "image/png")), "UNSUPPORTED_CAPABILITY"),
                    (await post(client, source_image=("source.png", png(), "image/png")), "INVALID_REQUEST"),
                    (await post(client, raw=json.dumps({**json.loads(request_json()), "mode": "IMAGE_TO_IMAGE", "strength": 0.6})), "INVALID_REQUEST"),
                    (await client.post("/v1/images/generate", json=json.loads(request_json())), "INVALID_REQUEST"),
                    (await client.post("/v1/images/generate", files=[("request_json", (None, request_json())), ("request_json", (None, request_json()))]), "INVALID_REQUEST"),
                ]
                for response, code in cases:
                    assert response.status_code == 422 and response.json()["error"]["code"] == code
                    assert "teapot" not in response.text and "traceback" not in response.text.lower()
                i2i = json.dumps({**json.loads(request_json()), "mode": "IMAGE_TO_IMAGE", "strength": 0.6})
                for content, mime in [(b"broken", "image/png"), (png(), "image/jpeg"), (png(), "image/svg+xml")]:
                    response = await post(client, raw=i2i, source_image=("input", content, mime))
                    assert response.status_code == 422
                    assert response.json()["error"]["code"] == "INVALID_REQUEST"
                assert not backend.calls
    asyncio.run(scenario())


def test_stream_body_limit_without_content_length():
    async def scenario():
        app = create_app(FakeBackend())

        async def body():
            yield b'--boundary\r\nContent-Disposition: form-data; name="source_image"; filename="source.png"\r\nContent-Type: image/png\r\n\r\n'
            for _ in range(MAX_BODY_BYTES // 65536 + 2):
                yield b"x" * 65536
            yield b"\r\n--boundary--\r\n"

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            response = await client.post("/v1/images/generate", content=body(),
                                         headers={"content-type": "multipart/form-data; boundary=boundary"})
            assert response.status_code == 413
            assert response.json()["error"]["code"] == "INVALID_REQUEST"
    asyncio.run(scenario())


def test_not_ready_and_failed_load():
    class FailedBackend(FakeBackend):
        def load(self):
            self.loads += 1
            raise RuntimeError("private/path internal failure")

    async def scenario():
        backend = FailedBackend()
        app = create_app(backend)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            assert (await client.get("/health")).status_code == 503
            assert (await post(client)).json()["error"]["code"] == "MODEL_NOT_READY"
            async with app.router.lifespan_context(app):
                await ready(app)
                health = await client.get("/health")
                assert health.status_code == 503 and health.json()["state"] == "failed"
                assert "private" not in health.text
                assert (await post(client)).json()["error"]["code"] == "MODEL_NOT_READY"
                assert backend.loads == 1
    asyncio.run(scenario())


def test_timeout_does_not_release_slot_while_worker_is_running():
    async def scenario():
        backend = FakeBackend()
        backend.release.clear()
        app = create_app(backend, timeout_seconds=0.1)
        async with app.router.lifespan_context(app):
            await ready(app)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                first = asyncio.create_task(post(client))
                assert await asyncio.to_thread(backend.entered.wait, 2)
                assert (await client.get("/health")).json()["state"] == "busy"
                assert (await post(client)).status_code == 429
                response = await first
                assert response.status_code == 504 and response.json()["error"]["code"] == "TIMEOUT"
                assert (await post(client)).status_code == 429
                inflight = app.state.image_service.inflight
                backend.release.set()
                await asyncio.gather(inflight, return_exceptions=True)
                assert (await client.get("/health")).json()["state"] == "ready"
                assert backend.loads == 1 and len(backend.calls) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("exception,code,status", [
    (RuntimeError("private traceback data"), "GENERATION_FAILED", 500),
    (MemoryError("private CUDA details"), "RESOURCE_EXHAUSTED", 503),
    (TimeoutError("private timeout details"), "TIMEOUT", 504),
])
def test_generation_errors_are_normalized(exception, code, status):
    async def scenario():
        backend = FakeBackend()
        backend.failure = exception
        app = create_app(backend)
        async with app.router.lifespan_context(app):
            await ready(app)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                response = await post(client)
                assert response.status_code == status and response.json()["error"]["code"] == code
                assert "private" not in response.text
    asyncio.run(scenario())


@pytest.mark.parametrize("placement,free_gib,budget_gib", [
    ("cuda", 60, 40), ("model_cpu_offload", 60, 40), ("cuda", 12, 4),
])
def test_real_backend_wrapper_loads_once_and_shares_components(monkeypatch, placement, free_gib, budget_gib):
    """Exercise our Diffusers wrapper with dependency spies, never download/import Torch."""
    events = []

    class Pipeline:
        def __init__(self, name, shared=None):
            self.name = name
            for key in ("transformer", "vae", "text_encoder", "text_encoder_2"):
                setattr(self, key, getattr(shared, key) if shared else object())

        @classmethod
        def from_pretrained(cls, path, **kwargs):
            events.append(("load", path, kwargs))
            return cls("t2i")

        @classmethod
        def from_pipe(cls, original, **kwargs):
            assert kwargs.get("torch_dtype") == "BF16 sentinel", "from_pipe defaults to FP32"
            events.append(("share", original))
            return cls("i2i", original)

        def to(self, device):
            events.append(("to", device))
            return self

        def set_progress_bar_config(self, **kwargs):
            pass

        def enable_model_cpu_offload(self):
            events.append(("offload", self.name))

        def remove_all_hooks(self):
            events.append(("remove_hooks", self.name))

        def __call__(self, **kwargs):
            events.append(("infer", self.name, kwargs))
            kwargs["callback_on_step_end"](self, 0, 0, {"latents": "fake"})
            return SimpleNamespace(images=[Image.new("RGB", (kwargs["width"], kwargs["height"]))])

    class Generator:
        def __init__(self, device):
            assert device == "cpu"

        def manual_seed(self, seed):
            events.append(("seed", seed))
            return self

    cuda = SimpleNamespace(is_available=lambda: True, is_bf16_supported=lambda: True,
                           synchronize=lambda: None, get_device_name=lambda n: "Fake GB10",
                           reset_peak_memory_stats=lambda: None,
                           mem_get_info=lambda: (free_gib * 1024**3, 120 * 1024**3),
                           set_per_process_memory_fraction=lambda value: events.append(("memory_budget", value)))
    torch = SimpleNamespace(cuda=cuda, bfloat16="BF16 sentinel", __version__="test-torch",
                            version=SimpleNamespace(cuda="test-cuda"),
                            Generator=Generator, inference_mode=nullcontext)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "diffusers", SimpleNamespace(FluxPipeline=Pipeline, FluxImg2ImgPipeline=Pipeline))
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr("services.flux_image.backend.importlib.metadata.version", lambda name: "test-version")
    monkeypatch.setattr(FluxBackend, "resources", lambda self: {"cuda_allocated_bytes": 1})
    # Restore all offline environment settings when the test ends.
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        monkeypatch.setenv(name, "1")
    backend = FluxBackend(placement)
    info = backend.load()
    assert info["placement"] == placement
    assert ("memory_budget", budget_gib / 120) in events
    assert backend.t2i.transformer is backend.i2i.transformer
    load = next(event for event in events if event[0] == "load")
    assert load[1] == "/home/Developer/models/image/flux-dev"
    assert load[2] == {"torch_dtype": "BF16 sentinel", "local_files_only": True, "use_safetensors": True}
    import time
    source = Image.new("RGB", (1024, 1024))
    for mode in ["TEXT_TO_IMAGE", "IMAGE_TO_IMAGE", "TEXT_TO_IMAGE"]:
        request = parse_request(request_json(mode=mode, **({"strength": 0.6} if mode == "IMAGE_TO_IMAGE" else {})))
        output, metrics = backend.generate(request, source if mode == "IMAGE_TO_IMAGE" else None,
                                           threading.Event(), time.monotonic() + 10)
        assert output.startswith(b"\x89PNG") and metrics["inference_seconds"] >= 0
    assert sum(event[0] == "load" for event in events) == 1
    calls = [event for event in events if event[0] == "infer"]
    assert [call[1] for call in calls] == ["t2i", "i2i", "t2i"]
    assert calls[1][2]["image"] is source and calls[1][2]["strength"] == 0.6
    assert "image" not in calls[0][2] and "strength" not in calls[0][2]
    if placement == "cuda":
        assert ("to", "cuda") in events and not any(event[0] == "offload" for event in events)
    else:
        assert [event for event in events if event[0] == "offload"] == [("offload", "t2i"), ("offload", "i2i"), ("offload", "t2i")]
        assert [event for event in events if event[0] == "remove_hooks"] == [("remove_hooks", "t2i"), ("remove_hooks", "i2i")]
        assert not any(event[0] == "to" for event in events)


def test_dependency_install_pins_distribution_not_torch_module_version(monkeypatch):
    from services.flux_image import install

    module_version = "2.14.0a0+4fdf77b940.nv26.08"
    distribution_version = "2.14.0a0+4fdf77b940.nv26.8.63802676"
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(__version__=module_version))
    monkeypatch.setattr(install, "sys", SimpleNamespace(prefix="venv", base_prefix="base", executable=sys.executable))
    monkeypatch.setattr("importlib.metadata.version", lambda name: distribution_version)
    calls = []
    monkeypatch.setattr(install.subprocess, "run", lambda args, **kwargs: calls.append(args))
    install.main()
    assert f"torch=={distribution_version}" in calls[0]
    assert f"torch=={module_version}" not in calls[0]
    assert calls[1][-2:] == [module_version, distribution_version]
    assert "FluxImg2ImgPipeline" in calls[2][-1]


def test_dependency_install_uses_explicit_offline_cache(monkeypatch):
    from services.flux_image import install

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(__version__="nv-module"))
    monkeypatch.setattr(install, "sys", SimpleNamespace(prefix="venv", base_prefix="base", executable=sys.executable))
    monkeypatch.setattr("importlib.metadata.version", lambda name: "nv-wheel")
    monkeypatch.setattr(Path, "is_dir", lambda path: path.name == "wheels")
    calls = []
    monkeypatch.setattr(install.subprocess, "run", lambda args, **kwargs: calls.append(args))
    install.main()
    assert "--no-index" in calls[0] and "--find-links" in calls[0]
