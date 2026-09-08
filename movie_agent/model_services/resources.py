"""Runtime policy and observations; memory units are bytes in one unified pool."""

from datetime import datetime
from enum import StrEnum
from pathlib import Path
import os

from dotenv import dotenv_values
from pydantic import Field

from movie_agent.domain import ContractModel, ResourceClass, new_id, utc_now

GiB = 1024 ** 3


class ModelResidency(StrEnum):
    UNKNOWN = "unknown"
    UNLOADED = "unloaded"
    LOADING = "loading"
    RESIDENT = "resident"
    BUSY = "busy"
    EVICTING = "evicting"
    FAILED = "failed"


class ModelRuntimeProfile(ContractModel):
    service_id: str
    provider_id: str
    model_profile_id: str
    runtime_kind: str = "docker"
    estimated_resident_bytes: int = Field(default=0, ge=0)
    estimated_peak_bytes: int = Field(ge=0)
    observed_peak_bytes: int | None = Field(default=None, ge=0)
    estimate_basis: str
    startup_cost_seconds: float = Field(default=0, ge=0)
    warmup_cost_seconds: float = Field(default=0, ge=0)
    concurrency_limit: int = Field(default=1, ge=1)
    supports_hot_unload: bool = False
    supports_model_unload: bool = False
    requires_exclusive_runtime: bool = False
    lifecycle_policy: str = "warm_ttl"


class LeaseStatus(StrEnum):
    ACQUIRED = "acquired"
    EXECUTING = "executing"
    UNCERTAIN = "uncertain"
    RELEASED = "released"
    INVALIDATED = "invalidated"


class ResourceLease(ContractModel):
    lease_id: str = Field(default_factory=lambda: new_id("lease"))
    project_id: str
    job_id: str
    service_id: str
    continuity_chain_id: str | None = None
    reserved_memory_bytes: int = Field(ge=0)
    resource_class: ResourceClass
    status: LeaseStatus = LeaseStatus.ACQUIRED
    remote_prompt_id: str | None = None
    inference_started: bool = False
    acquired_at: datetime = Field(default_factory=utc_now)
    released_at: datetime | None = None


class ResourceSnapshot(ContractModel):
    total_unified_memory_bytes: int = Field(gt=0)
    available_unified_memory_bytes: int = Field(ge=0)
    system_reserve_bytes: int = Field(default=0, ge=0)
    safety_margin_bytes: int = Field(default=0, ge=0)
    active_services: list[str] = Field(default_factory=list)
    active_resource_leases: list[ResourceLease] = Field(default_factory=list)
    # Diagnostic counters are never added to the unified memory pool.
    diagnostics: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utc_now)


class SchedulerDecision(ContractModel):
    decision_id: str = Field(default_factory=lambda: new_id("decision"))
    project_id: str
    job_id: str
    selected_service_id: str
    resource_snapshot: ResourceSnapshot | None = None
    required_reservation_bytes: int = Field(ge=0)
    actions: list[str] = Field(default_factory=list)
    admitted: bool = False
    reason: str
    timestamp: datetime = Field(default_factory=utc_now)


class ResourceObservation(ContractModel):
    job_id: str
    service_id: str
    resource_before: ResourceSnapshot
    resource_peak: ResourceSnapshot | None = None
    resource_after: ResourceSnapshot | None = None
    # Sampled process-wide pressure is not a reliable per-model allocation peak.
    observed_peak_bytes: int | None = None
    peak_is_sampled: bool = True
    startup_seconds: float = 0
    warmup_seconds: float = 0
    execution_seconds: float = 0
    release_seconds: float = 0
    outcome: str = "success"
    timestamp: datetime = Field(default_factory=utc_now)


class ResourceRuntimeSettings(ContractModel):
    enabled: bool = True
    system_reserve_gib: float = Field(default=4, ge=0, allow_inf_nan=False)
    safety_margin_gib: float = Field(default=8, ge=0, allow_inf_nan=False)
    warm_idle_ttl: float = Field(default=300, ge=0)
    telemetry_interval: float = Field(default=30, ge=1)
    service_start_timeout: float = Field(default=600, gt=0)
    service_stop_timeout: float = Field(default=90, gt=0)
    max_affinity_batch: int = Field(default=3, ge=1)
    ssh_host: str = "106.13.186.155"
    ssh_port: int = Field(default=6081, ge=1, le=65535)
    ssh_user: str = "Developer"
    ssh_key_path: str = str(Path.home() / ".ssh" / "id_ed25519")
    state_path: str = "workspace/_resource_runtime/spark-state.json"

    @classmethod
    def from_env(cls, path=".env", environ=None):
        values = {**dotenv_values(path), **(os.environ if environ is None else environ)}
        return cls.model_validate({name: values[f"MOVIE_AGENT_RESOURCE_{name.upper()}"]
            for name in cls.model_fields
            if values.get(f"MOVIE_AGENT_RESOURCE_{name.upper()}") not in (None, "")})
