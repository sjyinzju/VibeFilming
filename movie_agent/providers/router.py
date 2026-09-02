"""Capability-driven deterministic provider router."""

from __future__ import annotations

from movie_agent.domain import ProviderCapability, ProviderSelection, RoutingRequest
from movie_agent.providers.base import Provider


class ModelRouter:
    """Select providers using task, strategy, quality, and resource constraints."""

    def __init__(self, providers: list[Provider]) -> None:
        self._providers = {provider.provider_id: provider for provider in providers}

    async def select(self, request: RoutingRequest) -> ProviderSelection:
        candidates: list[tuple[Provider, ProviderCapability]] = []
        for provider in self._providers.values():
            if not await provider.health():
                continue
            capability = await provider.capabilities()
            if request.task not in capability.tasks:
                continue
            if request.resource_class not in capability.resource_classes:
                continue
            if capability.quality_profiles and request.quality_profile not in capability.quality_profiles:
                continue
            if request.strategy_type and request.strategy_type not in capability.generation_strategies:
                continue
            if not self._supports_required(capability, request.required_capabilities):
                continue
            candidates.append((provider, capability))
        if not candidates:
            raise LookupError("No provider satisfies the routing request")
        provider, capability = sorted(candidates, key=lambda pair: pair[0].provider_id)[0]
        return ProviderSelection(
            provider_id=provider.provider_id,
            capability=capability,
            reason="Selected deterministically from healthy providers satisfying all constraints.",
        )

    def get(self, provider_id: str) -> Provider:
        """Resolve a selected provider without exposing registry internals."""

        return self._providers[provider_id]

    @staticmethod
    def _supports_required(capability: ProviderCapability, required: list[str]) -> bool:
        flags = {
            "reference_images": capability.accepts_reference_images,
            "first_frame": capability.accepts_first_frame,
            "last_frame": capability.accepts_last_frame,
            "cancellation": capability.supports_cancellation,
            "seed": capability.supports_seed,
        }
        return all(flags.get(item, item in capability.tasks) for item in required)

