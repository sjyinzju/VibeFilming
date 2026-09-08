"""Explicit resource environment for real provider transports backed by fake HTTP."""

from movie_agent.domain import ProviderKind, ResourceClass
from movie_agent.media import MediaModality, ProviderCapabilities, ResourceProfile
from movie_agent.model_services import ModelManager, ModelServiceDescriptor, MockModelService
from movie_agent.model_services.coordinator import RuntimeCoordinator
from movie_agent.model_services.resources import GiB, ModelRuntimeProfile, ResourceSnapshot


def fake_resource_runtime(provider_id):
    class Telemetry:
        async def snapshot(self):
            return ResourceSnapshot(total_unified_memory_bytes=128 * GiB,
                                    available_unified_memory_bytes=112 * GiB)
    manager = ModelManager()
    manager.register(MockModelService(ModelServiceDescriptor(
        service_id=provider_id, modality=MediaModality.VIDEO, endpoint="mock://fake-http",
        capabilities=ProviderCapabilities(provider_id=provider_id, kind=ProviderKind.VIDEO,
                                         modalities=[MediaModality.VIDEO]),
        resource_profile=ResourceProfile(resource_class=ResourceClass.HEAVY),
        runtime_profile=ModelRuntimeProfile(service_id=provider_id, provider_id=provider_id,
            model_profile_id="fake-http", estimated_peak_bytes=GiB, estimate_basis="test fixture"))))
    return RuntimeCoordinator(manager, Telemetry())
