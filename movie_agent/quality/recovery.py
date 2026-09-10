"""Provider-neutral recovery plans and candidate acceptance contracts."""
from typing import Literal
from pydantic import Field, model_validator
from movie_agent.domain import ContractModel, QualityProfile
from movie_agent.media import ImagePurpose, MediaReference, ReferenceType
from movie_agent.quality.references import ReferenceRole


class CandidateAcceptancePolicy(ContractModel):
    minimum_seconds: float = Field(default=20, gt=0)
    minimum_shots: int = Field(default=3, ge=1)
    required_scene_ids: list[str] = Field(default_factory=list)
    required_dialogue_shot_ids: list[str] = Field(default_factory=list)
    export_before_optional_work: bool = True


class ReferenceRecoveryAction(ContractModel):
    node_id: str
    subject_id: str
    reference_type: ReferenceType
    artifact_id: str
    purpose: ImagePurpose
    strategy: Literal['reuse_selected', 'inspect_existing', 'generate']
    instruction: str = ''
    expected_description: str | None = None
    requirements: list[str] = Field(default_factory=list)
    priority: int = 20
    roles: list[ReferenceRole] = Field(default_factory=list)


class RecoveryPlan(ContractModel):
    plan_revision: str
    namespace: str = 'recovery'
    entry_node_id: str = 'shot_gate'
    reference_actions: list[ReferenceRecoveryAction] = Field(default_factory=list)
    reference_gate_nodes: dict[str, str] = Field(default_factory=dict)
    shot_priorities: dict[str, int] = Field(default_factory=dict)
    candidate: CandidateAcceptancePolicy = Field(default_factory=CandidateAcceptancePolicy)
    quality_profile: QualityProfile = QualityProfile.DRAFT
    resource_admission_attempts: int = Field(default=3,ge=1,le=3)
    resource_retry_delay_seconds: float = Field(default=30,ge=0,le=60)

    @model_validator(mode='after')
    def unique_actions(self):
        if len({a.node_id for a in self.reference_actions})!=len(self.reference_actions):
            raise ValueError('Recovery actions need unique node identities')
        return self


class AcceptedReferenceBinding(ContractModel):
    subject_id: str
    role: ReferenceRole
    reference: MediaReference
    known_limitations: list[str] = Field(default_factory=list)


class HumanReferenceAcceptance(ContractModel):
    acceptance_id: str
    workflow_node_id: str
    bindings: list[AcceptedReferenceBinding] = Field(min_length=1)
    user_statement: str = Field(min_length=1)
    question: str = Field(min_length=1)
    superseded_review_ids: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def exact_role_bindings(self):
        keys=[(b.subject_id,b.role) for b in self.bindings]
        if len(keys)!=len(set(keys)):raise ValueError('Each accepted semantic role needs one exact binding')
        if any(b.reference.version is None or b.reference.sha256 is None for b in self.bindings):
            raise ValueError('Human acceptance must pin every version and hash')
        return self
