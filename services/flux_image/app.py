"""Loopback HTTP service. No Agent imports, filesystem uploads, or model downloads."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import time
import uuid
import warnings
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from threading import Event

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response

from .backend import FluxBackend
from .contracts import (
    API_VERSION, MAX_BODY_BYTES, MAX_JSON_BYTES, MAX_SOURCE_BYTES, MAX_SOURCE_PIXELS,
    ErrorCode, GenerateRequest, ServiceError, capabilities, parse_request,
)

logger = logging.getLogger("flux_image")


def error_response(error: ServiceError, request_id: str) -> JSONResponse:
    return JSONResponse({"error": {"code": error.code, "message": error.message,
                                   "request_id": request_id}}, status_code=error.status,
                        headers={"X-Request-Id": request_id, "Cache-Control": "no-store"})


class RequestBoundary:
    """Bound the actual streamed body, including chunked uploads, before disk spooling."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        consumed = 0

        async def limited_receive():
            nonlocal consumed
            message = await receive()
            consumed += len(message.get("body", b""))
            if consumed > MAX_BODY_BYTES:
                raise ServiceError(ErrorCode.INVALID_REQUEST, "Multipart body exceeds the size limit", 413)
            return message

        async def tagged_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = [(key, value) for key, value in message["headers"]
                                      if key != b"x-request-id"] + [
                    (b"x-request-id", request_id.encode()), (b"x-content-type-options", b"nosniff")]
            await send(message)

        await self.app(scope, limited_receive, tagged_send)


def decode_source(content: bytes, mime: str, request: GenerateRequest) -> Image.Image:
    formats = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as source:
                if (source.format != formats.get(mime) or source.width * source.height > MAX_SOURCE_PIXELS
                        or getattr(source, "n_frames", 1) != 1):
                    raise ValueError("Unsupported image properties")
                source.verify()
            with Image.open(io.BytesIO(content)) as source:
                result = ImageOps.exif_transpose(source).convert("RGB")
                result = result.resize((request.width, request.height), Image.Resampling.LANCZOS)
                result.load()
                return result
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise ServiceError(ErrorCode.INVALID_REQUEST, "source_image must be a valid single-frame PNG, JPEG, or WebP within the limits") from None


class ImageService:
    def __init__(self, backend, timeout_seconds: float):
        if timeout_seconds <= 0:
            raise ValueError("Generation timeout must be positive")
        self.backend, self.timeout_seconds = backend, timeout_seconds
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="flux-gpu")
        self.state = "starting"
        self.load_count = 0
        self.load_seconds: float | None = None
        self.backend_info: dict = {}
        self.load_error: str | None = None
        self.load_task: asyncio.Task | None = None
        self.inflight: asyncio.Future | None = None
        self.cancelled: Event | None = None

    def start(self) -> None:
        if self.load_task is not None:
            raise RuntimeError("Service startup may only run once")
        self.load_task = asyncio.create_task(self._load())

    async def _load(self):
        started = time.perf_counter()
        self.load_count += 1
        try:
            self.backend_info = await asyncio.get_running_loop().run_in_executor(self.executor, self.backend.load)
            self.state = "ready"
        except Exception as exc:
            logger.exception("FLUX model startup failed")
            self.load_error = self.backend.normalize_error(exc).code
            self.state = "failed"
        finally:
            self.load_seconds = round(time.perf_counter() - started, 3)
            logger.info(json.dumps({"event": "model_load", **self.health()}))

    def health(self) -> dict:
        return {"api_version": API_VERSION, "service": "flux-direct-image", "model": "FLUX.1-dev",
                "state": self.state, "ready": self.state in {"ready", "busy"},
                "model_load_count": self.load_count, "model_load_seconds": self.load_seconds,
                "load_error_code": self.load_error, "generation_timeout_seconds": self.timeout_seconds,
                "runtime": self.backend_info}

    def _generate(self, request, content, mime, cancelled, deadline, request_id):
        source = None
        started = time.perf_counter()
        try:
            if content is not None:
                source = decode_source(content, mime, request)
            png, metrics = self.backend.generate(request, source, cancelled, deadline)
            if cancelled.is_set() or time.monotonic() >= deadline:
                raise ServiceError(ErrorCode.TIMEOUT, "Generation deadline exceeded", 504)
            # Detect broken runtime outputs, not just a claimed MIME type.
            with Image.open(io.BytesIO(png)) as image:
                if image.format != "PNG" or image.size != (request.width, request.height):
                    raise RuntimeError("Invalid backend PNG output")
                image.verify()
            metrics = {**metrics, "generation_seconds": round(time.perf_counter() - started, 3),
                       "width": request.width, "height": request.height, "bytes": len(png),
                       "sha256": hashlib.sha256(png).hexdigest(), "seed": request.seed}
            logger.info(json.dumps({"event": "generation_completed", "request_id": request_id,
                                    "mode": request.mode, "steps": request.steps, **metrics}))
            return png, metrics
        except Exception as exc:
            logger.exception("Generation failed request_id=%s", request_id)
            raise self.backend.normalize_error(exc) from None
        finally:
            if source is not None:
                source.close()

    async def generate(self, request, content, mime, request_id):
        if self.state == "busy":
            raise ServiceError(ErrorCode.RESOURCE_EXHAUSTED, "Service is busy; retry after the current request finishes", 429)
        if self.state != "ready":
            raise ServiceError(ErrorCode.MODEL_NOT_READY, "Model is not ready", 503)
        # No await between checking readiness and taking the single admission slot.
        self.state = "busy"
        cancelled = self.cancelled = Event()
        future = self.inflight = asyncio.get_running_loop().run_in_executor(
            self.executor, self._generate, request, content, mime, cancelled,
            time.monotonic() + self.timeout_seconds, request_id,
        )

        def finished(done):
            # Retrieve any late exception after the HTTP caller times out.
            if not done.cancelled():
                done.exception()
            self.inflight = None
            if self.state == "busy":
                self.state = "ready"

        future.add_done_callback(finished)
        try:
            return await asyncio.wait_for(asyncio.shield(future), self.timeout_seconds)
        except TimeoutError:
            cancelled.set()
            logger.warning("Generation HTTP deadline exceeded request_id=%s; slot retained until worker exits", request_id)
            raise ServiceError(ErrorCode.TIMEOUT, "Generation deadline exceeded; worker may still be stopping", 504) from None
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def close(self):
        self.state = "stopping"
        if self.cancelled:
            self.cancelled.set()
        if self.load_task:
            await self.load_task
        self.state = "stopping"
        if self.inflight:
            await asyncio.gather(self.inflight, return_exceptions=True)
        self.executor.shutdown(wait=True)


def create_app(backend=None, *, timeout_seconds: float = 600, placement: str = "cuda") -> FastAPI:
    service = ImageService(backend if backend is not None else FluxBackend(placement), timeout_seconds)

    @asynccontextmanager
    async def lifespan(app):
        service.start()
        yield
        await service.close()

    app = FastAPI(title="FLUX Direct Image Service", version=API_VERSION, lifespan=lifespan)
    app.state.image_service = service
    app.add_middleware(RequestBoundary)

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError):
        return error_response(exc, request.state.request_id)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(HTTPException)
    async def invalid_request(request: Request, exc):
        status = exc.status_code if isinstance(exc, HTTPException) else 422
        return error_response(ServiceError(ErrorCode.INVALID_REQUEST, "Invalid HTTP request", status), request.state.request_id)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        logger.error("Unhandled HTTP error request_id=%s", request.state.request_id, exc_info=exc)
        return error_response(ServiceError(ErrorCode.GENERATION_FAILED, "Internal service failure", 500), request.state.request_id)

    @app.get("/health")
    async def health():
        payload = service.health()
        return JSONResponse(payload, status_code=200 if payload["ready"] else 503)

    @app.get("/capabilities")
    async def get_capabilities():
        return capabilities()

    @app.post("/v1/images/generate", response_class=Response, responses={
        200: {"description": "One generated PNG; measurements in X-Flux-* headers", "content": {"image/png": {}}},
    }, openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {
        "schema": {"type": "object", "required": ["request_json"], "properties": {
            "request_json": {"type": "string", "description": "JSON described by GET /capabilities request_schema"},
            "source_image": {"type": "string", "format": "binary"},
            "reference_files[]": {"type": "array", "items": {"type": "string", "format": "binary"},
                                   "description": "Reserved; rejected in v1"},
        }}}}}})
    async def generate(http_request: Request):
        if http_request.headers.get("content-type", "").split(";", 1)[0].lower() != "multipart/form-data":
            raise ServiceError(ErrorCode.INVALID_REQUEST, "Use multipart/form-data with request_json")
        async with http_request.form(max_files=4, max_fields=8, max_part_size=MAX_JSON_BYTES) as form:
            if "reference_files[]" in form or "reference_files" in form:
                raise ServiceError(ErrorCode.UNSUPPORTED_CAPABILITY, "Reference conditioning is not supported; reference_files[] is reserved")
            for key in form:
                if key not in {"request_json", "source_image"}:
                    raise ServiceError(ErrorCode.UNSUPPORTED_CAPABILITY, "Unsupported multipart field")
                if len(form.getlist(key)) != 1:
                    raise ServiceError(ErrorCode.INVALID_REQUEST, "Duplicate multipart fields are not allowed")
            raw = form.get("request_json")
            if not isinstance(raw, str):
                raise ServiceError(ErrorCode.INVALID_REQUEST, "request_json must be a text field")
            request = parse_request(raw)
            upload = form.get("source_image")
            content, mime = None, None
            if request.mode == "TEXT_TO_IMAGE" and upload is not None:
                raise ServiceError(ErrorCode.INVALID_REQUEST, "source_image is only valid for IMAGE_TO_IMAGE")
            if request.mode == "IMAGE_TO_IMAGE":
                if not isinstance(upload, UploadFile):
                    raise ServiceError(ErrorCode.INVALID_REQUEST, "IMAGE_TO_IMAGE requires source_image")
                mime = upload.content_type
                if mime not in {"image/png", "image/jpeg", "image/webp"}:
                    raise ServiceError(ErrorCode.INVALID_REQUEST, "Unsupported source_image MIME type")
                content = await upload.read(MAX_SOURCE_BYTES + 1)
                if not content or len(content) > MAX_SOURCE_BYTES:
                    raise ServiceError(ErrorCode.INVALID_REQUEST, "source_image is empty or exceeds 20 MiB", 413)
            png, metrics = await service.generate(request, content, mime, http_request.state.request_id)
        return Response(png, media_type="image/png", headers={
            "Cache-Control": "no-store", "Content-Disposition": 'inline; filename="generated.png"',
            "X-Flux-Seed": str(request.seed), "X-Flux-Mode": request.mode,
            "X-Flux-Model-Load-Seconds": str(service.load_seconds),
            "X-Flux-Inference-Seconds": str(metrics["inference_seconds"]),
            "X-Flux-Generation-Seconds": str(metrics["generation_seconds"]),
            "X-Flux-Width": str(metrics["width"]), "X-Flux-Height": str(metrics["height"]),
            "X-Flux-SHA256": metrics["sha256"],
            "X-Flux-Resources": json.dumps(metrics["resources"], separators=(",", ":")),
        })

    return app
