"""Capability routing, provider factories, binary storage, and model services."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from movie_agent.domain import EventType, ProviderKind, QualityProfile, ResourceClass
from movie_agent.execution import LocalEventBus
from movie_agent.media import (
    ImageCapabilities,
    LocalBinaryArtifactStore,
    MediaModality,
    MediaRoutingRequest,
    ProviderCapabilities,
    ResourceProfile,
    artifact_uri,
    parse_artifact_uri,
)
from movie_agent.model_services import ModelManager, ModelServiceDescriptor, MockModelService
from movie_agent.providers import (
    MediaProviderSettings,
    MediaRouter,
    MockImageProvider,
    ProviderFactory,
    ProviderRegistry,
    normalize_media_provider_error,
)


def test_binary_artifact_store_uri_versioning_limits_and_deletion(tmp_path: Path) -> None:
    store = LocalBinaryArtifactStore(tmp_path / "media", max_size_bytes=16)
    first = store.put("image_1", 1, b"png", mime_type="image/png", extension="png")
    assert first.uri == "artifact://image_1/v1"
    assert store.open(first.uri).read() == b"png"
    assert store.exists(first.uri) and store.size(first.uri) == 3
    assert parse_artifact_uri(artifact_uri("image_1", 2)) == ("image_1", 2)
    with pytest.raises(ValueError):
        store.put("../escape", 1, b"bad", mime_type="image/png", extension="png")
    with pytest.raises(ValueError):
        store.put("large", 1, b"x" * 17, mime_type="image/png", extension="png")
    assert not store.delete_if_unreferenced(first.uri, {first.uri})
    assert store.delete_if_unreferenced(first.uri, set())
    assert not store.exists(first.uri)


def test_capability_registry_router_and_mock_real_config() -> None:
    async def scenario() -> None:
        registry = ProviderRegistry()
        registry.register(MockImageProvider())
        router = MediaRouter(registry)
        selection = await router.select(MediaRoutingRequest(
            modality=MediaModality.IMAGE, task="image",
            required_capabilities=["image_edit"], quality_profile=QualityProfile.HIGH,
            resource_class=ResourceClass.MEDIUM,
        ))
        assert selection.provider_id == "mock-image"
        assert router.capabilities.get("mock-image").image.image_edit

    asyncio.run(scenario())
    settings = MediaProviderSettings.from_env({
        "MOVIE_AGENT_IMAGE_PROVIDER": "real",
        "MOVIE_AGENT_VIDEO_PROVIDER": "mock",
    })
    assert settings.image_provider == "real" and settings.video_provider == "mock"
    with pytest.raises(LookupError, match="register a real adapter"):
        ProviderFactory.defaults().build_registry(settings)


def test_model_service_manager_lifecycle_and_events() -> None:
    async def scenario() -> None:
        events = LocalEventBus()
        capabilities = ProviderCapabilities(
            provider_id="future-image-provider", kind=ProviderKind.IMAGE,
            modalities=[MediaModality.IMAGE], tasks=["image"],
            image=ImageCapabilities(text_to_image=True),
            resource_profiles=[ResourceProfile(resource_class=ResourceClass.MEDIUM)],
        )
        descriptor = ModelServiceDescriptor(
            service_id="image-service", modality=MediaModality.IMAGE,
            endpoint="mock://not-running-any-model", capabilities=capabilities,
            resource_profile=ResourceProfile(resource_class=ResourceClass.MEDIUM),
        )
        service = MockModelService(descriptor, event_bus=events)
        manager = ModelManager()
        manager.register(service)
        assert not await service.health()
        assert (await manager.start("image-service")).value == "ready"
        assert await manager.select(MediaModality.IMAGE, ResourceClass.MEDIUM, ["text_to_image"]) is service
        assert (await manager.stop("image-service")).value == "stopped"
        assert len(events.events(EventType.MODEL_SERVICE_STATUS_CHANGED)) == 4

    asyncio.run(scenario())


def test_media_error_normalization_never_leaks_raw_backend_details() -> None:
    result = normalize_media_provider_error(
        "request_1", RuntimeError("CUDA out of memory at /secret/driver/path")
    )
    assert result.error_type.value == "internal"
    assert result.error_message == "media_provider_internal"
    assert "CUDA" not in result.model_dump_json()
