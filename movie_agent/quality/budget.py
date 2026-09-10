"""Durable reservation before dispatch; uncertain attempts consume the hard budget."""
from enum import StrEnum
from hashlib import sha256
import json
from threading import RLock

from pydantic import Field
from movie_agent.domain import ContractModel, Provenance


class WorkKind(StrEnum):
    VIDEO = 'video_regenerate'
    FRAME_EDIT = 'frame_edit'
    FRAME = 'frame_regenerate'
    INSPECT = 'vlm_inspection'
    TTS = 'tts'
    MUSIC = 'music'
    POST = 'post_only'


class QualityBudget(ContractModel):
    h3_total: int = Field(default=18, ge=1)
    h3_per_shot: int = Field(default=2, ge=1, le=2)
    frame_edits_per_chain: int = Field(default=2, ge=0, le=2)
    inspection_revisions: int = Field(default=3, ge=1, le=3)
    tts_per_cue_revision: int = Field(default=2, ge=1, le=2)
    music_per_scene: int = Field(default=2, ge=1, le=2)
    critic_dispatches_per_policy: int = Field(default=3, ge=1, le=3)
    critic_dispatches_total_per_subject: int = Field(default=6, ge=1, le=6)
    material_failures_per_policy: int = Field(default=2, ge=1, le=2)
    frame_generations_per_chain: int = Field(default=3, ge=1, le=3)


class BudgetExhausted(RuntimeError):
    pass


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class QualityBudgetLedger:
    def __init__(self, store, budget=None):
        self.store, self.budget = store, budget or QualityBudget()
        self._lock = RLock()

    def records(self):
        return [self.store.read_structured(a) for a in self.store.list_versions('quality_compute_ledger')]

    def ensure_inspection_available(self, subject, request=None):
        with self._lock:
            if request is not None and request.quality_policy_revision:
                authorization=self._authorize_policy(request)
                dispatches=self.critic_records(subject, request.quality_policy_revision)
                configured=[d for d in dispatches if d['critic_config_revision']==request.critic_config_revision]
                if (len(configured)>=self.budget.critic_dispatches_per_policy
                    or len(dispatches)>=self.budget.critic_dispatches_total_per_subject):
                    raise BudgetExhausted(f'Critic/provider budget exhausted for {subject}; material history preserved')
                failures=[r for r in self.critic_outcomes() if r['subject']==subject
                          and r['quality_policy_revision']==request.quality_policy_revision and r['outcome']=='material_rejected']
                limit=authorization.get('material_failure_overrides',{}).get(subject,self.budget.material_failures_per_policy)
                if not isinstance(limit,int) or not 1<=limit<=3:
                    raise BudgetExhausted('Authorized material budget exceeds the finite hard limit')
                if len(failures)>=limit:
                    raise BudgetExhausted(f'Material quality budget exhausted for {subject}')
                return
            if sum(r['kind']==WorkKind.INSPECT.value and r['subject']==subject for r in self.records())>=self.budget.inspection_revisions:
                raise BudgetExhausted(f'vlm_inspection budget exhausted for {subject}; human review required')

    def _authorize_policy(self, request):
        saved=self.store.get('quality_recovery_authorization') or self.store.get('p5r_recovery_authorization')
        authorization=self.store.read_structured(saved) if saved else {}
        if (request.quality_policy_revision not in authorization.get('policy_revisions', [])
            or request.recovery_authorization != authorization.get('recovery_authorization')
            or request.critic_config_revision != authorization.get('critic_config_revision')):
            raise BudgetExhausted('Policy revision requires explicit persisted recovery authorization')
        return authorization

    def critic_records(self, subject=None, revision=None):
        return [r for a in self.store.list_versions('p5r_critic_dispatch')
                if (r:=self.store.read_structured(a))
                and (subject is None or r['subject']==subject)
                and (revision is None or r['quality_policy_revision']==revision)]

    def critic_outcomes(self):
        return [self.store.read_structured(a) for a in self.store.list_versions('p5r_critic_outcome')]

    def reserve_critic(self, request, inspection_key):
        subject=request.video_artifact_id or request.image_artifact_id
        with self._lock:
            self.ensure_inspection_available(subject, request)
            if any(r['key']==inspection_key for r in self.critic_records()):
                raise BudgetExhausted('Unrecovered critic dispatch already exists; no automatic replay')
            record={'key':inspection_key,'subject':subject,'version':request.target_artifact_version,
                    'sha256':request.target_sha256,'quality_policy_revision':request.quality_policy_revision,
                    'critic_config_revision':request.critic_config_revision,
                    'critic_configuration_fingerprint':request.critic_configuration_fingerprint,
                    'recovery_authorization':request.recovery_authorization,'inspection_revision':request.inspection_revision}
            self.store.create_structured('p5r_critic_dispatch', record,
                provenance=Provenance(project_id=request.project_id,tool='critic_budget',parameters=record),
                metadata={'purpose':'critic_dispatch_reservation'})
            return record

    def finish_critic(self, record, *, result=None, error=None):
        with self._lock:
            if any(r['key']==record['key'] for r in self.critic_outcomes()):
                return
            outcome='critic_invalid_or_transport'
            if result:
                outcome=('committed_pass' if result.decision.value=='pass' else
                    'material_rejected' if any(i.severity.value in {'major','critical'} for i in result.issues)
                    else 'committed_inconclusive')
            content={**record,'outcome':outcome,'material_quality_consumed':outcome=='material_rejected',
                     'inspection_result_id':result.result_id if result else None,
                     'diagnostic':str(error)[:500] if error else None,
                     'provider_attempts':getattr(error,'attempts',[]) if error else result.provider_metadata.get('attempts',[])}
            self.store.create_structured('p5r_critic_outcome',content,
                metadata={'purpose':'critic_budget_outcome'},provenance=Provenance(tool='critic_budget',parameters={'immutable_history':True}))

    def reserve(self, kind, subject, *, inputs, config, attempt=1):
        kind = WorkKind(kind)
        key = fingerprint(dict(kind=kind.value, subject=subject, inputs=inputs, config=config, attempt=attempt))
        with self._lock:
            records = self.records()
            previous = next((r for r in records if r['key'] == key), None)
            if previous:
                # The caller must recover a successful artifact; never silently re-dispatch.
                return key, False
            counts = [r for r in records if r['kind'] == kind.value]
            limit = {WorkKind.VIDEO:self.budget.h3_per_shot, WorkKind.FRAME_EDIT:self.budget.frame_edits_per_chain,
                     WorkKind.FRAME:self.budget.frame_generations_per_chain,
                     WorkKind.INSPECT:self.budget.inspection_revisions, WorkKind.TTS:self.budget.tts_per_cue_revision,
                     WorkKind.MUSIC:self.budget.music_per_scene}.get(kind)
            if kind == WorkKind.VIDEO and len(counts) >= self.budget.h3_total:
                raise BudgetExhausted('H3 global budget exhausted; assemble accepted footage')
            if limit is not None and sum(r['subject'] == subject for r in counts) >= limit:
                raise BudgetExhausted(f'{kind.value} budget exhausted for {subject}; human review required')
            self.store.create_structured('quality_compute_ledger', {'key':key, 'kind':kind.value,
                'subject':subject, 'input_fingerprint':fingerprint(inputs), 'config_fingerprint':fingerprint(config),
                'attempt':attempt, 'status':'reserved'}, metadata={'purpose':'quality_compute_reservation'},
                provenance=Provenance(tool='quality_budget', parameters={'dispatch_reservation':True}))
            return key, True


class RepairCostEstimate(ContractModel):
    kind: WorkKind
    relative_compute: float = Field(ge=0)
    basis: str = 'configured relative work units; not financial cost or measured seconds'


class RepairCostPolicy(ContractModel):
    costs: dict[WorkKind, float] = Field(default_factory=lambda: {
        WorkKind.POST:1, WorkKind.TTS:2, WorkKind.MUSIC:3, WorkKind.FRAME_EDIT:5,
        WorkKind.FRAME:6, WorkKind.VIDEO:20})

    def estimate(self, kind):
        return RepairCostEstimate(kind=kind, relative_compute=self.costs[kind])

    def route(self, issue_type, *, kontext_available):
        kind = {'identity_drift':WorkKind.FRAME_EDIT, 'wardrobe_drift':WorkKind.FRAME_EDIT,
                'prop_drift':WorkKind.FRAME_EDIT, 'scene_drift':WorkKind.FRAME_EDIT,
                'lighting_mismatch':WorkKind.FRAME_EDIT, 'camera_motion_mismatch':WorkKind.VIDEO,
                'action_incomplete':WorkKind.VIDEO, 'motion_failure':WorkKind.VIDEO,
                'dialogue_voice':WorkKind.TTS, 'music_masking':WorkKind.POST,
                'music_mismatch':WorkKind.MUSIC, 'subtitle':WorkKind.POST, 'pacing':WorkKind.POST}.get(issue_type)
        if kind == WorkKind.FRAME_EDIT and not kontext_available:
            kind = WorkKind.FRAME
        return self.estimate(kind) if kind else None

    def route_image(self, *, structural, previous_semantic_edit_failed=False, minor_nonblocking=False,
                    preserve_reference_identity=False, reference_conditioning_available=False):
        if minor_nonblocking:
            return None
        if preserve_reference_identity:
            # Unconditioned text-to-image cannot preserve an accepted identity bundle.
            if previous_semantic_edit_failed or not reference_conditioning_available:return None
            return self.estimate(WorkKind.FRAME_EDIT)
        # The deployed single-source editor has no mask/inpaint support. A failed
        # semantic change cannot be routed back to the same approximate edit.
        return self.estimate(WorkKind.FRAME if structural or previous_semantic_edit_failed else WorkKind.FRAME_EDIT)
