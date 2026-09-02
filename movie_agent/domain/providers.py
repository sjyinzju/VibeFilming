"""Provider capability, request/result, and routing contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from movie_agent.domain.base import ContractModel, JSONValue, new_id, utc_now
from movie_agent.domain.cinematic import GenerationRequest, GenerationStrategyType
from movie_agent.domain.enums import (
    ProviderErrorType,
    ProviderKind,
    QualityProfile,
    ResourceClass,
)


class ProviderCapability(ContractModel):
    """Advertised provider behavior used by strategy planning and routing."""

    provider_id: str
    kind: ProviderKind
    tasks: list[str] = Field(default_factory=list)
    generation_strategies: list[GenerationStrategyType] = Field(default_factory=list)
    accepts_reference_images: bool = False
    accepts_first_frame: bool = False
    accepts_last_frame: bool = False
    supports_cancellation: bool = True
    supports_seed: bool = False
    resource_classes: list[ResourceClass] = Field(default_factory=list)
    quality_profiles: list[QualityProfile] = Field(default_factory=list)
    max_duration_seconds: float | None = Field(default=None, gt=0)


class ProviderRequest(ContractModel):
    """Uniform provider submission envelope."""

    provider_request_id: str = Field(default_factory=lambda: new_id("providerreq"))
    provider_id: str
    generation_request: GenerationRequest
    submitted_at: datetime = Field(default_factory=utc_now)


class ProviderResult(ContractModel):
    """Normalized provider outcome independent of SDK exception shapes."""

    provider_request_id: str
    success: bool
    retryable: bool = False
    error_type: ProviderErrorType | None = None
    error_message: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, JSONValue] = Field(default_factory=dict)
    latency_seconds: float = Field(default=0.0, ge=0.0)


class ProviderSelection(ContractModel):
    """Deterministic routing decision and auditable rationale."""

    selection_id: str = Field(default_factory=lambda: new_id("selection"))
    provider_id: str
    capability: ProviderCapability
    reason: str


class RoutingRequest(ContractModel):
    """Input to a ModelRouter without concrete model names."""

    task: str
    quality_profile: QualityProfile
    required_capabilities: list[str] = Field(default_factory=list)
    strategy_type: GenerationStrategyType | None = None
    resource_class: ResourceClass

