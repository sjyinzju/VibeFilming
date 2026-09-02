"""Quality evaluation and finite repair planning contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from movie_agent.domain.base import ContractModel, new_id, utc_now
from movie_agent.domain.enums import (
    EvaluationIssueType,
    EvaluationLayer,
    IssueSeverity,
    RepairActionType,
)


class EvaluationIssue(ContractModel):
    """Structured, evidenced defect discovered by a critic or deterministic QC."""

    issue_id: str = Field(default_factory=lambda: new_id("issue"))
    issue_type: EvaluationIssueType
    severity: IssueSeverity
    message: str
    evidence: list[str] = Field(default_factory=list)
    suggested_action: RepairActionType | None = None
    shot_id: str | None = None
    artifact_id: str | None = None


class Evaluation(ContractModel):
    """Scored quality result; pass/fail is derived from score and issues."""

    evaluation_id: str = Field(default_factory=lambda: new_id("evaluation"))
    layer: EvaluationLayer
    target_artifact_id: str | None = None
    target_shot_id: str | None = None
    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    issues: list[EvaluationIssue] = Field(default_factory=list)
    summary: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class RepairAction(ContractModel):
    """One bounded, executable response to a classified issue."""

    action_id: str = Field(default_factory=lambda: new_id("repairaction"))
    action_type: RepairActionType
    issue_ids: list[str] = Field(default_factory=list)
    target_shot_id: str | None = None
    target_artifact_id: str | None = None
    rationale: str
    consumes_retry: bool = True


class RepairPlan(ContractModel):
    """Finite repair plan with an explicit retry budget and escalation path."""

    repair_plan_id: str = Field(default_factory=lambda: new_id("repairplan"))
    evaluation_id: str
    actions: list[RepairAction] = Field(default_factory=list)
    retry_budget: int = Field(ge=0)
    retry_count: int = Field(default=0, ge=0)
    exhausted: bool = False
    requires_human: bool = False

