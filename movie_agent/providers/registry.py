"""Media provider registration, configuration, capability inventory, and routing."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, field_validator

from movie_agent.media import MediaModality, MediaProviderSelection, MediaRoutingRequest, ProviderCapabilities
from movie_agent.providers.media import (
    MediaProvider, MockAudioProvider, MockImageProvider, MockPostProcessor,
    MockVideoProvider, MockVisionProvider,
)


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
        for provider in self.providers.all():
            capability = self.capabilities.get(provider.provider_id)
            if request.modality not in capability.modalities or request.task not in capability.tasks:
                continue
            if capability.quality_profiles and request.quality_profile not in capability.quality_profiles:
                continue
            if request.resource_class not in [item.resource_class for item in capability.resource_profiles]:
                continue
            if not await provider.health() or not self._supports(capability, request.required_capabilities):
                continue
            candidates.append((provider, capability))
        if not candidates:
            raise LookupError("No media provider satisfies the routing request")
        provider, capability = sorted(candidates, key=lambda item: item[0].provider_id)[0]
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
    model_config = ConfigDict(extra="forbid")
    image_provider: str = "mock"
    video_provider: str = "mock"
    vision_provider: str = "mock"
    audio_provider: str = "mock"
    post_provider: str = "mock"

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
            field: values.get(f"MOVIE_AGENT_{field.upper()}") or "mock"
            for field in cls.model_fields
        })


ProviderBuilder = Callable[[], MediaProvider]


class ProviderFactory:
    def __init__(self) -> None:
        self._builders: dict[tuple[MediaModality, str], ProviderBuilder] = {}

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
    def defaults(cls) -> "ProviderFactory":
        factory = cls()
        factory.register(MediaModality.IMAGE, "mock", MockImageProvider)
        factory.register(MediaModality.VIDEO, "mock", MockVideoProvider)
        factory.register(MediaModality.VISION, "mock", MockVisionProvider)
        factory.register(MediaModality.AUDIO, "mock", MockAudioProvider)
        factory.register(MediaModality.POST, "mock", MockPostProcessor)
        return factory

    def build_registry(self, settings: MediaProviderSettings) -> ProviderRegistry:
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
