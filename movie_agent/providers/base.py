"""Uniform provider ABCs and error normalization."""

from __future__ import annotations

from abc import ABC, abstractmethod

from movie_agent.domain import (
    ProviderCapability,
    ProviderErrorType,
    ProviderRequest,
    ProviderResult,
)


class ProviderFailure(RuntimeError):
    """Provider exception carrying normalized error semantics."""

    def __init__(
        self,
        message: str,
        error_type: ProviderErrorType = ProviderErrorType.INTERNAL,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.retryable = retryable


def normalize_provider_error(provider_request_id: str, error: BaseException) -> ProviderResult:
    """Convert SDK/transport exceptions into the stable ProviderResult contract."""

    if isinstance(error, ProviderFailure):
        error_type = error.error_type
        retryable = error.retryable
    elif isinstance(error, TimeoutError):
        error_type = ProviderErrorType.TIMEOUT
        retryable = True
    elif isinstance(error, ValueError):
        error_type = ProviderErrorType.INVALID_REQUEST
        retryable = False
    else:
        error_type = ProviderErrorType.INTERNAL
        retryable = False
    return ProviderResult(
        provider_request_id=provider_request_id,
        success=False,
        retryable=retryable,
        error_type=error_type,
        error_message=str(error),
    )


class Provider(ABC):
    """Base async provider contract shared by all media and reasoning kinds."""

    provider_id: str

    @abstractmethod
    async def health(self) -> bool:
        """Return whether the provider can currently accept work."""

    @abstractmethod
    async def capabilities(self) -> ProviderCapability:
        """Return stable capability metadata for strategy/routing."""

    @abstractmethod
    async def submit(self, request: ProviderRequest) -> ProviderResult:
        """Submit a provider request and return a normalized result."""

    @abstractmethod
    async def status(self, provider_request_id: str) -> ProviderResult | None:
        """Return the latest normalized status/result for a request."""

    @abstractmethod
    async def cancel(self, provider_request_id: str) -> bool:
        """Request cancellation and report whether it was accepted."""


class LLMProvider(Provider):
    """Provider capable of language/reasoning work."""


class VisionProvider(Provider):
    """Provider capable of image/video understanding work."""


class ImageProvider(Provider):
    """Provider capable of still-image generation or editing."""


class VideoProvider(Provider):
    """Provider capable of video generation or transformation."""


class AudioProvider(Provider):
    """Provider capable of speech, music, or sound generation."""

