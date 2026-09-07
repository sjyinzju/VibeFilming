"""HTTP adapter for the independently deployed FLUX service (no GPU dependencies)."""
from __future__ import annotations

import hashlib
import io
import json
import math
import time
from typing import Literal
from urllib.parse import urlsplit

import httpx
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from movie_agent.domain import ProviderErrorType, ProviderKind, Provenance, QualityProfile, ResourceClass
from movie_agent.media.contracts import (
    ImageCapabilities, ImageGenerationMode, ImageGenerationRequest, ImageGenerationResult,
    MediaDimensions, MediaEncoding, MediaModality, ProviderCapabilities, ResourceProfile,
    ReferenceType,
)
from movie_agent.media.transport import MediaReferenceBinaryResolver
from movie_agent.providers.base import ProviderFailure
from movie_agent.providers.media import BinaryPayload, ImageProvider, ProviderMediaResponse


class FluxWireRequest(BaseModel):
    """Service API 1.0; independent of the remote service's Python package."""
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["TEXT_TO_IMAGE", "IMAGE_TO_IMAGE"]
    prompt: str = Field(min_length=1, max_length=4000)
    width: int = Field(ge=256, le=1024, multiple_of=16)
    height: int = Field(ge=256, le=1024, multiple_of=16)
    steps: int = Field(default=28, ge=1, le=50)
    guidance_scale: float = Field(default=3.5, ge=0, le=20, allow_inf_nan=False)
    seed: int = Field(default=42, ge=0, le=2**63 - 1)
    strength: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_conditioning(self):
        if not self.prompt.strip():
            raise ValueError("blank prompt")
        if self.mode == "IMAGE_TO_IMAGE":
            if self.strength is None or math.floor(self.steps * self.strength) < 1:
                raise ValueError("Img2Img requires at least one denoising step")
        elif self.strength is not None:
            raise ValueError("strength requires Img2Img")
        return self


def validate_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("FLUX endpoint must be an HTTP(S) service URL without credentials, query, or fragment")
    return value.rstrip("/")


class FluxDirectImageProvider(ImageProvider):
    provider_id = "flux_direct"
    model_service_id = "flux-direct-image"

    def __init__(self, *, endpoint="http://127.0.0.1:9001", timeout=660,
                 resolver: MediaReferenceBinaryResolver | None = None, transport=None):
        self.endpoint = validate_endpoint(endpoint)
        self.timeout = float(timeout)
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("FLUX timeout must be positive and finite")
        self.resolver, self.transport = resolver, transport
        self._results: dict[str, ImageGenerationResult] = {}

    def _client(self):
        return httpx.AsyncClient(base_url=self.endpoint + "/", transport=self.transport,
                                 timeout=httpx.Timeout(self.timeout, connect=min(10, self.timeout)),
                                 follow_redirects=False, trust_env=False)

    async def health(self) -> bool:
        try:
            async with self._client() as client:
                response = await client.get("health", timeout=min(5, self.timeout))
            data = response.json()
            return response.status_code == 200 and data.get("ready") is True and data.get("service") == self.model_service_id
        except (httpx.HTTPError, ValueError, AttributeError):
            return False

    async def capabilities(self):
        return ProviderCapabilities(provider_id=self.provider_id, kind=ProviderKind.IMAGE,
            modalities=[MediaModality.IMAGE], tasks=["image", "frame"],
            image=ImageCapabilities(text_to_image=True, image_to_image=True,
                min_dimension=256, dimension_multiple=16, max_width=1024, max_height=1024),
            resource_profiles=[ResourceProfile(resource_class=item, expected_memory_gb=40,
                supports_concurrency=False) for item in ResourceClass],
            quality_profiles=list(QualityProfile), supports_cancellation=False,
            model_service_id=self.model_service_id)

    async def status(self, request_id):
        return self._results.get(request_id)

    async def cancel(self, request_id):
        # API 1.0 has no remote cancellation endpoint; local cancellation is best-effort.
        return False

    def _serialize(self, request: ImageGenerationRequest):
        unsupported = (request.mode not in {ImageGenerationMode.TEXT_TO_IMAGE, ImageGenerationMode.IMAGE_TO_IMAGE}
            or bool(request.references) or bool(request.mask_artifact_id)
            or bool(set(request.provider_parameters) - {"steps", "guidance_scale", "strength"})
            or any(item.required and item.capability not in {"text_to_image", "image_to_image"}
                   for item in request.required_capabilities))
        if unsupported:
            raise ProviderFailure("FLUX supports only T2I or a single explicit Img2Img source; other conditioning is unsupported",
                                  ProviderErrorType.UNSUPPORTED_CAPABILITY)
        if request.source_image and request.source_image.reference_type not in {
            ReferenceType.SOURCE_IMAGE, ReferenceType.FIRST_FRAME, ReferenceType.PREVIOUS_FRAME,
        }:
            raise ProviderFailure("Source must explicitly be an Img2Img source or generated continuity frame",
                                  ProviderErrorType.UNSUPPORTED_CAPABILITY)
        if (request.mode == ImageGenerationMode.IMAGE_TO_IMAGE) != (request.source_image is not None):
            raise ProviderFailure("Img2Img requires exactly one source_image; T2I cannot consume one", ProviderErrorType.INVALID_REQUEST)
        prompt = request.prompt_package.positive_prompt
        if request.prompt_package.negative_prompt:
            prompt += "\n[constraints] " + request.prompt_package.negative_prompt
        options = dict(request.provider_parameters)
        if request.mode == ImageGenerationMode.IMAGE_TO_IMAGE:
            options.setdefault("strength", 0.6)
        try:
            wire = FluxWireRequest(mode=request.mode.value.upper(), prompt=prompt,
                width=request.width, height=request.height, seed=request.seed if request.seed is not None else 42, **options)
        except ValidationError as error:
            raise ProviderFailure("FLUX request is outside the service parameter contract", ProviderErrorType.INVALID_REQUEST) from error
        return wire

    async def generate(self, request: ImageGenerationRequest) -> ProviderMediaResponse[ImageGenerationResult]:
        wire = self._serialize(request)
        resolved = None
        files = [("request_json", (None, wire.model_dump_json(exclude_none=True), "application/json"))]
        if request.source_image is not None:
            if self.resolver is None:
                raise ProviderFailure("Source artifact resolver is unavailable", ProviderErrorType.INVALID_REQUEST)
            try:
                resolved = self.resolver.resolve(request.source_image)
                if len(resolved.content) > 20 * 1024**2:
                    raise ValueError("source too large")
            except (KeyError, OSError, ValueError) as error:
                raise ProviderFailure("Source image artifact is missing or invalid", ProviderErrorType.INVALID_REQUEST) from error
            files.append(("source_image", (resolved.filename, resolved.content, resolved.mime_type)))
        started = time.perf_counter()
        try:
            async with self._client() as client:
                async with client.stream("POST", "v1/images/generate", files=files) as response:
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 8 * 1024**2:
                            raise ProviderFailure("FLUX response exceeds the PNG size limit", ProviderErrorType.MEDIA_CORRUPT)
                    if response.status_code != 200:
                        try:
                            code = json.loads(content)["error"]["code"]
                        except (ValueError, KeyError, TypeError):
                            code = "GENERATION_FAILED"
                        mapping = {item.value.upper(): item for item in ProviderErrorType}
                        kind = mapping.get(code, ProviderErrorType.GENERATION_FAILED)
                        if code == "UNSUPPORTED_MODE":
                            kind = ProviderErrorType.UNSUPPORTED_CAPABILITY
                        raise ProviderFailure(f"FLUX service: {kind.value}", kind)
                    headers = response.headers
            content = bytes(content)
        except httpx.TimeoutException as error:
            # Remote completion is uncertain: do not retry and generate duplicates.
            raise ProviderFailure("FLUX request timed out; remote work may still be active", ProviderErrorType.TIMEOUT) from error
        except httpx.HTTPError as error:
            raise ProviderFailure("PROVIDER_UNAVAILABLE: FLUX service transport failed", ProviderErrorType.UNAVAILABLE) from error
        try:
            if headers.get("content-type", "").split(";")[0] != "image/png":
                raise ValueError("not PNG")
            with Image.open(io.BytesIO(content)) as decoded:
                if decoded.format != "PNG" or decoded.size != (wire.width, wire.height) or getattr(decoded, "n_frames", 1) != 1:
                    raise ValueError("wrong image dimensions or format")
                decoded.verify()
            digest = hashlib.sha256(content).hexdigest()
            if headers["x-flux-sha256"] != digest or int(headers["x-flux-seed"]) != wire.seed or headers["x-flux-mode"] != wire.mode:
                raise ValueError("response identity mismatch")
            if (int(headers["x-flux-width"]), int(headers["x-flux-height"])) != (wire.width, wire.height):
                raise ValueError("header dimension mismatch")
            timings = {name: float(headers[f"x-flux-{name.replace('_', '-')}"])
                       for name in ("model_load_seconds", "inference_seconds", "generation_seconds")}
            if any(not math.isfinite(value) or value < 0 for value in timings.values()):
                raise ValueError("invalid timing")
        except (OSError, ValueError, KeyError, Image.DecompressionBombError) as error:
            raise ProviderFailure("FLUX returned an invalid PNG or inconsistent result metadata", ProviderErrorType.MEDIA_CORRUPT) from error
        parameters = {"mock": False, "model": "FLUX.1-dev", "endpoint": self.endpoint,
            "service": self.model_service_id, "service_api_version": "1.0",
            "service_request_id": headers.get("x-request-id"), "sha256": digest,
            "request_parameters": wire.model_dump(exclude_none=True),
            "negative_prompt_mapping": "positive_prompt_constraints" if request.prompt_package.negative_prompt else "none",
            "native_negative_conditioning": False, "source_artifact_uri": resolved.reference.artifact_uri if resolved else None,
            "source_sha256": hashlib.sha256(resolved.content).hexdigest() if resolved else None,
            "http_seconds": round(time.perf_counter() - started, 3), **timings}
        result = ImageGenerationResult(request_id=request.request_id, artifact_ids=[request.output_artifact_id],
            primary_artifact_id=request.output_artifact_id, provider_id=self.provider_id, model_service_id=self.model_service_id,
            seed=wire.seed, dimensions=MediaDimensions(width=wire.width, height=wire.height, aspect_ratio=request.aspect_ratio),
            encoding=MediaEncoding(mime_type="image/png", format="png", codec="png"), provider_metadata=parameters,
            provenance=Provenance(provider_id=self.provider_id, model_service_id=self.model_service_id,
                input_artifact_ids=[resolved.reference.artifact_id] if resolved else [], parameters=parameters))
        self._results[request.request_id] = result
        return ProviderMediaResponse(result, (BinaryPayload(request.output_artifact_id, content, "image/png", "png", request.purpose.value),))


class ComfyUIImageProvider(FluxDirectImageProvider):
    """Unavailable extension point only. No ComfyUI protocol or backend implementation."""
    provider_id = "comfyui"
    model_service_id = "comfyui-unimplemented"

    async def health(self):
        return False

    async def capabilities(self):
        capability = await super().capabilities()
        return capability.model_copy(update={"image": ImageCapabilities()})

    async def generate(self, request):
        raise ProviderFailure("PROVIDER_UNAVAILABLE: ComfyUIImageProvider is not implemented", ProviderErrorType.UNAVAILABLE)
