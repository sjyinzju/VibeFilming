"""Role configuration, invocation, context, validation, and result records."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from pydantic import Field
from movie_agent.domain import ContractModel, JSONValue, new_id, utc_now
from movie_agent.providers.inference import RoleInferencePolicy


class RoleId(StrEnum):
    """Stable identifiers for the six reasoning roles."""

    CREATIVE_PRODUCER = "creative_producer"
    STORY_ARCHITECT = "story_architect"
    SCREENWRITER = "screenwriter"
    VISUAL_DIRECTOR = "visual_director"
    DIRECTOR = "director"
    CINEMATOGRAPHER = "cinematographer"


class ContextPolicy(ContractModel):
    """Allowlist of brief fields and domain sources; never runtime or job state."""

    brief_fields: list[str]
    sources: list[str] = Field(default_factory=list)


class OutputPolicy(ContractModel):
    """Finite validation repair and bounded generation length."""

    repair_budget: int = Field(default=2, ge=0, le=5)
    max_tokens: int = Field(default=12000, ge=128, le=32768)


class RoleDefinition(ContractModel):
    """Provider-independent instruction, context policy, and named output schema."""

    role_id: RoleId
    target_schema: str
    instruction: str
    context_policy: ContextPolicy
    output_policy: OutputPolicy = Field(default_factory=OutputPolicy)
    inference_policy: RoleInferencePolicy = Field(default_factory=RoleInferencePolicy)


class ContractSchemaRevision(ContractModel):
    """One explicitly authorized schema revision, never a reset of prior attempts."""

    kind: Literal["CONTRACT_SCHEMA_REVISION"] = "CONTRACT_SCHEMA_REVISION"
    authorization_reference: str
    scene_id: str
    previous_invocation_id: str
    previous_result_hash: str
    request_schema_hash: str
    repair_budget: Literal[2] = 2
    stop_on_state_scope_conflict: Literal[True] = True
    created_at: datetime = Field(default_factory=utc_now)


class RequestContractTypeRevision(ContractModel):
    """Explicit ownership change from model snapshots to Core deterministic state."""

    kind: Literal["REQUEST_CONTRACT_TYPE_REVISION"] = "REQUEST_CONTRACT_TYPE_REVISION"
    authorization_reference: str
    scene_id: str
    parent_invocation_id: str
    parent_result_hash: str
    old_schema_hash: str
    new_schema_hash: str
    ownership_change: Literal["CORE_CANONICAL_STATE_LLM_LOCAL_DELTA"] = "CORE_CANONICAL_STATE_LLM_LOCAL_DELTA"
    mapper_version: str
    repair_budget: Literal[2] = 2
    created_at: datetime = Field(default_factory=utc_now)


class SemanticContractRevision(ContractModel):
    """Single scene-local semantic revision with independently bounded repairs."""
    kind: Literal["SEMANTIC_CONTRACT_REVISION"] = "SEMANTIC_CONTRACT_REVISION"
    authorization_reference: str
    scene_id: str
    parent_invocation_id: str
    parent_result_hash: str
    source_output_hash: str
    request_schema_hash: str
    terminal_target_hash: str
    repair_budget: Literal[2] = 2
    created_at: datetime = Field(default_factory=utc_now)


class RoleInvocation(ContractModel):
    """Stable invocation identity, including the scene scope where appropriate."""

    invocation_id: str = Field(default_factory=lambda: new_id("role"))
    role_id: RoleId
    project_id: str
    node_id: str
    scene_id: str | None = None
    contract_revision: ContractSchemaRevision | None = None
    request_contract_revision: RequestContractTypeRevision | None = None
    semantic_revision: SemanticContractRevision | None = None


class RoleContext(ContractModel):
    """Deterministic JSON projection with source references and content hash."""

    payload: dict[str, JSONValue]
    source_ids: list[str]
    context_hash: str
    context_version: str = "1"


class OutputErrorCode(StrEnum):
    """Validation defects, separate from media repair issues."""

    SCHEMA_INVALID = "schema_invalid"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    UNKNOWN_ENTITY_ID = "unknown_entity_id"
    CONSTRAINT_VIOLATION = "constraint_violation"
    DURATION_BUDGET_ERROR = "duration_budget_error"
    BROKEN_REFERENCE = "broken_reference"
    SEMANTIC_CONFLICT = "semantic_conflict"


class OutputIssue(ContractModel):
    """Machine-readable validation error supplied to bounded output repair."""

    code: OutputErrorCode
    path: str
    message: str


class ValidationReport(ContractModel):
    """Schema and semantic validation outcome, with honest validation coverage."""

    issues: list[OutputIssue] = Field(default_factory=list)
    unverified_constraints: list[str] = Field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.issues


class RoleAttempt(ContractModel):
    """Safe inference provenance; no secret, reasoning, or raw HTTP response."""

    request_id: str
    prompt_hash: str
    latency_seconds: float
    token_usage: dict[str, int] = Field(default_factory=dict)
    validation: ValidationReport
    raw_output: str | None = None
    request_prompt: str | None = None
    timestamp: datetime = Field(default_factory=utc_now)


class ValidationReplay(ContractModel):
    """Non-inference revalidation of one preserved provider output after a code fix."""

    source_request_id: str
    source_output_hash: str
    reason: Literal["DETERMINISTIC_CODE_FIX_REVALIDATION"] = "DETERMINISTIC_CODE_FIX_REVALIDATION"
    mapper_version: str | None = None
    validation: ValidationReport
    timestamp: datetime = Field(default_factory=utc_now)


class RoleResult(ContractModel):
    """Persistable role result envelope; output is revalidated by its named schema."""

    invocation: RoleInvocation
    provider_id: str
    served_model: str | None = None
    context: RoleContext
    target_schema: str
    target_schema_version: str = "1.0.0"
    output: dict[str, JSONValue] | None = None
    attempts: list[RoleAttempt] = Field(default_factory=list)
    committed: bool = False
    failure_code: str | None = None
    pending_output: str | None = None
    terminal_repair_base: str | None = None
    inference_records: list["InferenceMetrics"] = Field(default_factory=list)
    validation_replays: list[ValidationReplay] = Field(default_factory=list)


class InferenceMetrics(ContractModel):
    """Safe per-request telemetry, including transport failures without validation attempts."""

    request_id: str
    role_id: RoleId
    scene_id: str | None = None
    context_chars: int
    prompt_chars: int
    input_token_estimate: int
    schema_chars: int
    max_output_tokens: int
    read_timeout: float
    total_timeout: float | None = None
    inactivity_timeout: float | None = None
    streaming: bool = False
    failure_phase: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    latency_seconds: float = 0
    provider_outcome: str = "submitted"
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None
    validation_result: str = "not_run"


RoleResult.model_rebuild()
