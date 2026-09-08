"""Basic model-service registry and lifecycle selection."""

from __future__ import annotations

import asyncio

from movie_agent.domain import ResourceClass
from movie_agent.media import MediaModality, ModelServiceStatus
from movie_agent.model_services.contracts import ModelService
from movie_agent.domain import ProviderErrorType
from movie_agent.providers.base import ProviderFailure


class ModelManager:
    def __init__(self) -> None:
        self._services: dict[str, ModelService] = {}

    def register(self, service: ModelService) -> None:
        service_id = service.descriptor.service_id
        if service_id in self._services:
            raise ValueError(f"duplicate model service {service_id}")
        self._services[service_id] = service

    def get(self, service_id: str) -> ModelService:
        try:
            return self._services[service_id]
        except KeyError as error:
            raise LookupError(service_id) from error

    def all(self) -> list[ModelService]:
        return list(self._services.values())

    async def statuses(self) -> dict[str, ModelServiceStatus]:
        return {service_id: await service.status() for service_id, service in self._services.items()}

    async def select(
        self,
        modality: MediaModality,
        resource_class: ResourceClass,
        required_capabilities: list[str] | None = None,
    ) -> ModelService:
        required = required_capabilities or []
        candidates: list[ModelService] = []
        for service in self._services.values():
            descriptor = service.descriptor
            if descriptor.modality != modality:
                continue
            if descriptor.resource_profile.resource_class != resource_class:
                continue
            flags: dict[str, bool] = {}
            for group in (descriptor.capabilities.image, descriptor.capabilities.video,
                          descriptor.capabilities.vision, descriptor.capabilities.audio):
                if group:
                    flags.update({name: value for name, value in group.model_dump().items()
                                  if isinstance(value, bool)})
            if not all(flags.get(item, item in descriptor.capabilities.tasks) for item in required):
                continue
            if await service.status() == ModelServiceStatus.READY:
                candidates.append(service)
        if not candidates:
            raise LookupError("no ready model service satisfies the request")
        return sorted(candidates, key=lambda item: item.descriptor.service_id)[0]

    async def start(self, service_id: str) -> ModelServiceStatus:
        return await self.get(service_id).start()

    async def stop(self, service_id: str) -> ModelServiceStatus:
        return await self.get(service_id).stop()

    async def ensure_ready(self, service_id: str, *, timeout: float = 600) -> ModelService:
        service = self.get(service_id)
        try:
            async with asyncio.timeout(timeout):
                state = await service.status()
                if state in {ModelServiceStatus.DRAINING, ModelServiceStatus.STOPPING}:
                    raise ProviderFailure("service is draining", ProviderErrorType.MODEL_NOT_READY)
                if state != ModelServiceStatus.READY or not await service.health():
                    await service.start()
                if not await service.health():
                    raise ProviderFailure("service health verification failed", ProviderErrorType.MODEL_NOT_READY)
                service.descriptor.status = ModelServiceStatus.READY
                return service
        except TimeoutError as error:
            service.descriptor.status = ModelServiceStatus.FAILED
            raise ProviderFailure("service start timeout", ProviderErrorType.TIMEOUT) from error
        except BaseException:
            service.descriptor.status = ModelServiceStatus.FAILED
            raise
