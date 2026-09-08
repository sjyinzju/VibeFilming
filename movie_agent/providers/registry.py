"""Media provider registration, configuration, capability inventory, and routing."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, field_validator

from movie_agent.media import (
    MediaModality,
    MediaProviderSelection,
    MediaRoutingRequest,
    ProviderCapabilities,
    VideoCapabilities,
)
from movie_agent.providers.media import (
    MediaProvider, MockAudioProvider, MockImageProvider, MockPostProcessor,
    MockVideoProvider, MockVisionProvider,
)
from movie_agent.providers.base import ProviderFailure
from movie_agent.domain import ProviderErrorType


class MediaRoutingFailure(ProviderFailure, LookupError):
    """A routing failure is still a visible, normalized media job failure."""


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, MediaProvider] = {}

    def register(self, provider: MediaProvider) -> None:
        if provider.provider_id in self._providers:
            raise ValueError(f"duplicate media provider {provider.provider_id}")
        self._providers[provider.provider_id] = provider

    def get(self, provider_id: str) -> MediaProvider:
        try:
            return self._providers[provider_id]
        except KeyError as error:
            raise LookupError(f"unknown media provider {provider_id}") from error

    def all(self) -> list[MediaProvider]:
        return list(self._providers.values())


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, ProviderCapabilities] = {}

    def register(self, capabilities: ProviderCapabilities) -> None:
        self._capabilities[capabilities.provider_id] = capabilities

    def get(self, provider_id: str) -> ProviderCapabilities:
        return self._capabilities[provider_id]

    def all(self) -> list[ProviderCapabilities]:
        return [item.model_copy(deep=True) for item in self._capabilities.values()]


class MediaRouter:
    def __init__(self, providers: ProviderRegistry, capabilities: CapabilityRegistry | None = None) -> None:
        self.providers = providers
        self.capabilities = capabilities or CapabilityRegistry()

    async def refresh(self) -> None:
        for provider in self.providers.all():
            self.capabilities.register(await provider.capabilities())

    async def select(self, request: MediaRoutingRequest) -> MediaProviderSelection:
        await self.refresh()
        candidates: list[tuple[MediaProvider, ProviderCapabilities]] = []
        matching_unhealthy = False
        for provider in self.providers.all():
            capability = self.capabilities.get(provider.provider_id)
            if request.modality not in capability.modalities or request.task not in capability.tasks:
                continue
            if capability.quality_profiles and request.quality_profile not in capability.quality_profiles:
                continue
            if request.resource_class not in [item.resource_class for item in capability.resource_profiles]:
                continue
            managed = getattr(self, "runtime_coordinator", None)
            if not (managed and managed.settings.enabled and managed.service_for(provider.provider_id)) and not await provider.health():
                matching_unhealthy = True
                continue
            if not self._supports(capability, request.required_capabilities):
                continue
            candidates.append((provider, capability))
        if not candidates:
            raise MediaRoutingFailure(
                "PROVIDER_UNAVAILABLE: selected media service is unavailable" if matching_unhealthy
                else "No media provider supports the requested capabilities",
                ProviderErrorType.UNAVAILABLE if matching_unhealthy else ProviderErrorType.UNSUPPORTED_CAPABILITY)
        coordinator = getattr(self, "runtime_coordinator", None)
        def preference(item):
            sid = coordinator.service_for(item[0].provider_id) if coordinator else None
            warm = sid and coordinator.manager.get(sid).descriptor.status.value == "ready"
            return (-int(bool(warm)), item[0].provider_id)
        provider, capability = sorted(candidates, key=preference)[0]
        return MediaProviderSelection(
            provider_id=provider.provider_id, capabilities=capability,
            reason="Selected deterministically from healthy providers matching modality, capability, quality, and resource class.",
        )

    @staticmethod
    def _supports(capability: ProviderCapabilities, required: list[str]) -> bool:
        flags: dict[str, bool] = {"cancellation": capability.supports_cancellation}
        for group in (capability.image, capability.video, capability.vision, capability.audio):
            if group is not None:
                flags.update({name: bool(value) for name, value in group.model_dump().items()
                              if isinstance(value, bool)})
        return all(flags.get(item, item in capability.tasks) for item in required)


class MediaProviderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    image_provider: str = "mock"
    video_provider: str = "mock"
    vision_provider: str = "mock"
    vision_endpoint: str = "http://127.0.0.1:8001/v1"
    vision_model: str = "movie-agent-vision"
    vision_timeout: float = Field(default=600, gt=0, le=1800, allow_inf_nan=False)
    vision_media_remote_root: str = "/home/Developer/runtime/vlm-media"
    vision_max_tokens: int = Field(default=6000, ge=256, le=16000)
    vision_minimum_score: float = Field(default=0.80, ge=0, le=1)
    vision_profile_thresholds: dict[str, float] = Field(default_factory=dict)
    vision_structured_output: str = Field(default="json_schema", pattern=r"^(json_schema|json_object)$")
    vision_fast_frames: int = Field(default=24, ge=8, le=24)
    vision_full_frames: int = Field(default=40, ge=32, le=48)
    vision_video_transport: str = Field(default="jpeg_sequence", pattern=r"^(native|jpeg_sequence)$")
    vision_resident_gib: float = Field(default=88, ge=0, allow_inf_nan=False)
    vision_peak_gib: float = Field(default=92, gt=0, allow_inf_nan=False)
    vision_estimate_basis: str = "P4B GB10 2026-09-08: isolated Scene01 24-frame JPEG video plus two references, successful sampled pressure 86.42 GiB (CLI) / 86.52 GiB (browser); worst observed cold-start pressure 87.74 GiB. Resident rounded to 88 GiB; peak rounded up plus 4 GiB to 92 GiB. Separate P4A 12 GiB headroom remains. Sampled unified-memory pressure, not an allocator guarantee; FULL/targeted workloads unbenchmarked."
    audio_provider: str = "mock"
    post_provider: str = "mock"
    flux_endpoint: str = "http://127.0.0.1:9001"
    flux_timeout: float = Field(default=660, gt=0, allow_inf_nan=False)
    comfyui_endpoint: str = "http://127.0.0.1:8188"
    comfyui_timeout: float = Field(default=3600, gt=0, allow_inf_nan=False)
    comfyui_websocket_timeout: float = Field(default=3600, gt=0, allow_inf_nan=False)
    comfyui_workflow_profile: str = "minimax_h3_fl2va"

    @field_validator("flux_endpoint")
    @classmethod
    def validate_flux_endpoint(cls, value):
        from movie_agent.providers.flux_direct import validate_endpoint
        return validate_endpoint(value)

    @field_validator("vision_endpoint")
    @classmethod
    def validate_vision_endpoint(cls, value):
        from movie_agent.providers.flux_direct import validate_endpoint
        return validate_endpoint(value).rstrip("/")

    @field_validator("vision_profile_thresholds", mode="before")
    @classmethod
    def parse_thresholds(cls, value):
        import json
        return json.loads(value) if isinstance(value, str) else value

    @field_validator("comfyui_endpoint")
    @classmethod
    def validate_comfyui_endpoint(cls, value):
        from movie_agent.comfyui import validate_comfyui_endpoint
        return validate_comfyui_endpoint(value)

    @field_validator("comfyui_workflow_profile")
    @classmethod
    def validate_workflow_profile(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]*", value):
            raise ValueError("ComfyUI workflow profile must be a stable lower-case ID")
        return value

    @field_validator("image_provider", "video_provider", "vision_provider", "audio_provider", "post_provider")
    @classmethod
    def validate_binding(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", value):
            raise ValueError("provider binding must be a lower-case registry key")
        return value

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None, path: str | Path = ".env"
    ) -> "MediaProviderSettings":
        values = {**dotenv_values(path), **(os.environ if environ is None else environ)}
        return cls(**{
            field: values.get(f"MOVIE_AGENT_{field.upper()}") or definition.get_default(call_default_factory=True)
            for field, definition in cls.model_fields.items()
        })


ProviderBuilder = Callable[[], MediaProvider]


class ProviderFactory:
    def __init__(self) -> None:
        self._builders: dict[tuple[MediaModality, str], ProviderBuilder] = {}
        self._settings = MediaProviderSettings()

    def register(self, modality: MediaModality, binding: str, builder: ProviderBuilder) -> None:
        self._builders[(modality, binding)] = builder

    def create(self, modality: MediaModality, binding: str) -> MediaProvider:
        try:
            return self._builders[(modality, binding)]()
        except KeyError as error:
            raise LookupError(
                f"No {modality.value} provider is registered for binding '{binding}'. "
                "Install/register a real adapter before selecting it."
            ) from error

    @classmethod
    def defaults(
        cls,
        *,
        settings: MediaProviderSettings | None = None,
        resolver=None,
        comfyui_client=None,
        comfyui_workflows=None,
        comfyui_runtime_capabilities: VideoCapabilities | None = None,
    ) -> "ProviderFactory":
        from movie_agent.comfyui import ComfyUIClient, ComfyUIWorkflowRegistry
        from movie_agent.providers.comfyui_video import ComfyUIVideoProvider
        from movie_agent.providers.flux_direct import ComfyUIImageProvider, FluxDirectImageProvider
        settings = settings or MediaProviderSettings()
        factory = cls()
        factory._settings = settings
        factory.register(MediaModality.IMAGE, "mock", MockImageProvider)
        factory.register(MediaModality.IMAGE, "flux_direct", lambda: FluxDirectImageProvider(
            endpoint=factory._settings.flux_endpoint, timeout=factory._settings.flux_timeout, resolver=resolver))
        factory.register(MediaModality.IMAGE, "comfyui", ComfyUIImageProvider)
        factory.register(MediaModality.VIDEO, "mock", MockVideoProvider)
        factory.register(MediaModality.VIDEO, "comfyui", lambda: ComfyUIVideoProvider(
            client=comfyui_client or ComfyUIClient(
                endpoint=factory._settings.comfyui_endpoint,
                timeout=factory._settings.comfyui_timeout,
                websocket_timeout=factory._settings.comfyui_websocket_timeout,
            ),
            workflows=comfyui_workflows or ComfyUIWorkflowRegistry(),
            workflow_profile=factory._settings.comfyui_workflow_profile,
            resolver=resolver,
            runtime_capabilities=comfyui_runtime_capabilities or VideoCapabilities(
                first_frame=True,
                last_frame=True,
                first_last_frame=True,
                audio_generation=True,
                max_duration_seconds=15,
                max_width=1344,
                max_height=1344,
                supported_fps=[24],
            ),
        ))
        factory.register(MediaModality.VISION, "mock", MockVisionProvider)
        from movie_agent.providers.qwen3_vl import Qwen3VLVisionProvider
        factory.register(MediaModality.VISION, "qwen3_vl", lambda: Qwen3VLVisionProvider(
            settings=factory._settings, resolver=resolver))
        factory.register(MediaModality.AUDIO, "mock", MockAudioProvider)
        factory.register(MediaModality.POST, "mock", MockPostProcessor)
        return factory

    def build_registry(self, settings: MediaProviderSettings) -> ProviderRegistry:
        self._settings = settings
        registry = ProviderRegistry()
        bindings = {
            MediaModality.IMAGE: settings.image_provider,
            MediaModality.VIDEO: settings.video_provider,
            MediaModality.VISION: settings.vision_provider,
            MediaModality.AUDIO: settings.audio_provider,
            MediaModality.POST: settings.post_provider,
        }
        for modality, binding in bindings.items():
            registry.register(self.create(modality, binding))
        return registry
