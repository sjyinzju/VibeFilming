"""Shared primitives for versioned and JSON-serializable domain contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

SCHEMA_VERSION = "1.0.0"

JSONValue = JsonValue


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    """Create a stable opaque identifier with a human-readable type prefix."""

    return f"{prefix}_{uuid4().hex}"


class ContractModel(BaseModel):
    """Base for every public contract, including migration version metadata."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        use_enum_values=False,
        ser_json_timedelta="iso8601",
    )

    schema_version: str = Field(
        default=SCHEMA_VERSION,
        min_length=1,
        description="Version of this serialized contract for future migrations.",
    )


class Provenance(ContractModel):
    """Trace of the role, tool, provider, inputs, and decisions behind an output."""

    role: str | None = None
    tool: str | None = None
    provider_id: str | None = None
    prompt_package_id: str | None = None
    input_artifact_ids: list[str] = Field(default_factory=list)
    generation_strategy: str | None = None
    seed: int | None = None
    parameters: dict[str, JSONValue] = Field(default_factory=dict)
    retry_history: list[str] = Field(default_factory=list)
    evaluation_ids: list[str] = Field(default_factory=list)
    repair_plan_ids: list[str] = Field(default_factory=list)
