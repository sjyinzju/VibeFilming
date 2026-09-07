"""Versioned wire contract, independent of orchestration/domain contracts."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

API_VERSION = "1.0"
MODEL_PATH = "/home/Developer/models/image/flux-dev"
MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_BODY_BYTES = MAX_SOURCE_BYTES + 128 * 1024
MAX_SOURCE_PIXELS = 16_777_216
MAX_JSON_BYTES = 16_384


class ErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    MODEL_NOT_READY = "MODEL_NOT_READY"
    UNSUPPORTED_MODE = "UNSUPPORTED_MODE"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    GENERATION_FAILED = "GENERATION_FAILED"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    TIMEOUT = "TIMEOUT"


class ServiceError(Exception):
    def __init__(self, code: ErrorCode, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    mode: Literal["TEXT_TO_IMAGE", "IMAGE_TO_IMAGE"]
    prompt: str = Field(min_length=1, max_length=4000)
    width: int = Field(default=1024, ge=256, le=1024, multiple_of=16)
    height: int = Field(default=1024, ge=256, le=1024, multiple_of=16)
    steps: int = Field(default=28, ge=1, le=50)
    guidance_scale: float = Field(default=3.5, ge=0, le=20)
    seed: int = Field(default=42, ge=0, le=2**63 - 1)
    strength: float | None = Field(default=None, gt=0, le=1)

    @field_validator("prompt")
    @classmethod
    def nonblank_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value

    @model_validator(mode="after")
    def validate_strength(self) -> GenerateRequest:
        if self.mode == "IMAGE_TO_IMAGE":
            if self.strength is None or int(self.steps * self.strength) < 1:
                raise ValueError("IMAGE_TO_IMAGE requires strength with steps * strength >= 1")
        elif self.strength is not None:
            raise ValueError("strength is only valid for IMAGE_TO_IMAGE")
        return self


UNSUPPORTED_MODES = {
    "INPAINT", "OUTPAINT", "MULTI_REFERENCE", "CONTROLNET", "LORA", "KONTEXT",
    "FILL", "COMFYUI", "UPSCALE", "STRUCTURED_EDIT",
}
UNSUPPORTED_FIELDS = {
    "reference_files", "references", "multi_reference", "mask", "mask_image", "mask_url",
    "controlnet", "control_image", "lora", "loras", "lora_weights", "kontext", "fill",
    "workflow", "workflow_json", "comfyui", "structured_edit", "negative_prompt",
    "prompt_2", "num_images", "num_images_per_prompt", "image_url", "source_image_url",
}


def parse_request(raw: str) -> GenerateRequest:
    if len(raw.encode("utf-8")) > MAX_JSON_BYTES:
        raise ServiceError(ErrorCode.INVALID_REQUEST, "request_json is too large", 413)

    def unique_object(pairs: list[tuple]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique_object)
    except (ValueError, RecursionError):
        raise ServiceError(ErrorCode.INVALID_REQUEST, "request_json must be valid, unambiguous JSON") from None
    if not isinstance(value, dict):
        raise ServiceError(ErrorCode.INVALID_REQUEST, "request_json must be an object")
    mode = value.get("mode")
    if isinstance(mode, str) and mode in UNSUPPORTED_MODES:
        raise ServiceError(ErrorCode.UNSUPPORTED_CAPABILITY, "This model does not implement the requested capability")
    if not isinstance(mode, str) or mode not in {"TEXT_TO_IMAGE", "IMAGE_TO_IMAGE"}:
        raise ServiceError(ErrorCode.UNSUPPORTED_MODE, "mode must be TEXT_TO_IMAGE or IMAGE_TO_IMAGE")
    if UNSUPPORTED_FIELDS.intersection(value):
        raise ServiceError(ErrorCode.UNSUPPORTED_CAPABILITY, "Unsupported conditioning or generation option supplied")
    try:
        return GenerateRequest.model_validate(value)
    except ValidationError:
        # Never echo the input prompt, filenames, data URLs, or exception internals.
        raise ServiceError(ErrorCode.INVALID_REQUEST, "Request fields violate the published schema") from None


def capabilities() -> dict:
    return {
        "api_version": API_VERSION,
        "service": "flux-direct-image",
        "model": "FLUX.1-dev",
        "backend": "diffusers-direct",
        "dtype": "bfloat16",
        "text_to_image": True,
        "image_to_image": True,
        "inpaint": False,
        "outpaint": False,
        "multi_reference": False,
        "structured_edit": False,
        "controlnet": False,
        "lora": False,
        "kontext": False,
        "fill": False,
        "comfyui_workflow": False,
        "modes": ["TEXT_TO_IMAGE", "IMAGE_TO_IMAGE"],
        "output_mime_type": "image/png",
        "input_mime_types": ["image/png", "image/jpeg", "image/webp"],
        "max_source_bytes": MAX_SOURCE_BYTES,
        "max_source_pixels": MAX_SOURCE_PIXELS,
        "source_transform": "EXIF orientation, RGB, resize to requested dimensions (no crop)",
        "max_concurrency": 1,
        "queue_capacity": 0,
        "reference_files_policy": "reserved; any supplied part is rejected with UNSUPPORTED_CAPABILITY",
        "request_schema": GenerateRequest.model_json_schema(),
    }
