"""Model-service lifecycle contracts kept separate from provider invocation."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import Field

from movie_agent.domain import ContractModel, EventEnvelope, EventType, JSONValue, new_id
from movie_agent.execution.events import EventBus
from movie_agent.media import (
    MediaModality,
    ModelServiceStatus,
    ProviderCapabilities,
    ResourceProfile,
)


class ModelServiceDescriptor(ContractModel):
    service_id: str
    modality: MediaModality
    endpoint: str
    status: ModelServiceStatus = ModelServiceStatus.STOPPED
    capabilities: ProviderCapabilities
    resource_profile: ResourceProfile
    metadata: dict[str, JSONValue] = Field(default_factory=dict)


class ModelService(ABC):
    descriptor: ModelServiceDescriptor

    @abstractmethod
    async def health(self) -> bool: ...

    @abstractmethod
    async def start(self) -> ModelServiceStatus: ...

    @abstractmethod
    async def stop(self) -> ModelServiceStatus: ...

    @abstractmethod
    async def status(self) -> ModelServiceStatus: ...


class MockModelService(ModelService):
    """In-memory lifecycle fake; it never runs SSH, Docker, or a model."""

    def __init__(
        self,
        descriptor: ModelServiceDescriptor,
        *,
        event_bus: EventBus | None = None,
        project_id: str = "model-services",
        trace_id: str | None = None,
    ) -> None:
        self.descriptor = descriptor
        self.event_bus = event_bus
        self.project_id = project_id
        self.trace_id = trace_id or new_id("trace")

    async def health(self) -> bool:
        return self.descriptor.status in {ModelServiceStatus.READY, ModelServiceStatus.BUSY}

    async def start(self) -> ModelServiceStatus:
        if self.descriptor.status in {ModelServiceStatus.READY, ModelServiceStatus.BUSY}:
            return self.descriptor.status
        self._set(ModelServiceStatus.STARTING)
        return self._set(ModelServiceStatus.READY)

    async def stop(self) -> ModelServiceStatus:
        if self.descriptor.status == ModelServiceStatus.STOPPED:
            return ModelServiceStatus.STOPPED
        self._set(ModelServiceStatus.STOPPING)
        return self._set(ModelServiceStatus.STOPPED)

    async def status(self) -> ModelServiceStatus:
        return self.descriptor.status

    def _set(self, status: ModelServiceStatus) -> ModelServiceStatus:
        self.descriptor = self.descriptor.model_copy(update={"status": status})
        if self.event_bus:
            self.event_bus.emit(EventEnvelope(
                event_type=EventType.MODEL_SERVICE_STATUS_CHANGED,
                project_id=self.project_id,
                trace_id=self.trace_id,
                payload={"service_id": self.descriptor.service_id, "status": status.value},
            ))
        return status
