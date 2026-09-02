"""In-memory fake provider used for complete model-free workflows."""

from __future__ import annotations

import asyncio
from time import perf_counter

from movie_agent.domain import (
    GenerationStrategyType,
    ProviderCapability,
    ProviderErrorType,
    ProviderKind,
    ProviderRequest,
    ProviderResult,
    QualityProfile,
    ResourceClass,
    new_id,
)
from movie_agent.providers.base import Provider, normalize_provider_error


class MockProvider(Provider):
    """Deterministic provider with configurable latency and first-attempt failures."""

    def __init__(
        self,
        provider_id: str = "mock-provider",
        kind: ProviderKind = ProviderKind.VIDEO,
        latency_seconds: float = 0.0,
        fail_first_for_jobs: set[str] | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.kind = kind
        self.latency_seconds = latency_seconds
        self.fail_first_for_jobs = set(fail_first_for_jobs or set())
        self._attempts: dict[str, int] = {}
        self._results: dict[str, ProviderResult] = {}
        self._cancelled: set[str] = set()

    async def health(self) -> bool:
        return True

    async def capabilities(self) -> ProviderCapability:
        strategies = list(GenerationStrategyType) if self.kind == ProviderKind.VIDEO else []
        return ProviderCapability(
            provider_id=self.provider_id,
            kind=self.kind,
            tasks=["creative", "story", "image", "video", "audio", "critique", "post"],
            generation_strategies=strategies,
            accepts_reference_images=True,
            accepts_first_frame=True,
            accepts_last_frame=True,
            supports_cancellation=True,
            supports_seed=True,
            resource_classes=list(ResourceClass),
            quality_profiles=list(QualityProfile),
            max_duration_seconds=120,
        )

    async def submit(self, request: ProviderRequest) -> ProviderResult:
        started = perf_counter()
        request_id = request.provider_request_id
        job_id = request.generation_request.job_id
        self._attempts[job_id] = self._attempts.get(job_id, 0) + 1
        try:
            if request_id in self._cancelled:
                result = ProviderResult(
                    provider_request_id=request_id,
                    success=False,
                    retryable=False,
                    error_type=ProviderErrorType.CANCELLED,
                    error_message="mock request cancelled",
                )
            elif job_id in self.fail_first_for_jobs and self._attempts[job_id] == 1:
                result = ProviderResult(
                    provider_request_id=request_id,
                    success=False,
                    retryable=True,
                    error_type=ProviderErrorType.UNAVAILABLE,
                    error_message="intentional first-attempt mock failure",
                    latency_seconds=perf_counter() - started,
                )
            else:
                if self.latency_seconds:
                    await asyncio.sleep(self.latency_seconds)
                result = ProviderResult(
                    provider_request_id=request_id,
                    success=True,
                    artifact_ids=[new_id("mockartifact")],
                    metadata={"mock": True, "attempt": self._attempts[job_id]},
                    latency_seconds=perf_counter() - started,
                )
        except BaseException as error:
            result = normalize_provider_error(request_id, error)
        self._results[request_id] = result
        return result

    async def status(self, provider_request_id: str) -> ProviderResult | None:
        return self._results.get(provider_request_id)

    async def cancel(self, provider_request_id: str) -> bool:
        self._cancelled.add(provider_request_id)
        return True


FakeProvider = MockProvider

