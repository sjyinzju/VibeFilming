from types import SimpleNamespace as NS
import pytest
from movie_agent.artifacts import LocalArtifactStore
from movie_agent.media import VisionInspectionRequest, ReferenceType, VisionDecision
from movie_agent.quality.reference_policy import (ReferenceAcceptancePolicy, scoped_reference_requirements,
    REVISION, CRITIC_REVISION, AUTHORIZATION)
from movie_agent.quality.vision import VisionInspectionDraft, inspection_fingerprint
from movie_agent.quality.budget import QualityBudgetLedger, BudgetExhausted, RepairCostPolicy, WorkKind
from movie_agent.quality.references import ReferenceIdentitySet, ReferenceResolver
from tests.test_p5_quality import shot, anchor
from movie_agent.quality.references import ReferenceRole


def request(role='prop'):
    return VisionInspectionRequest(job_id='j',project_id='p',image_artifact_id='same',target_artifact_version=2,
        target_sha256='a'*64,profiles=['image_quality','artifact_detection','prompt_alignment'],output_artifact_id='review',
        reference_role=role,quality_policy_revision=REVISION,critic_config_revision=CRITIC_REVISION,
        recovery_authorization=AUTHORIZATION)


def draft(**changes):
    payload={'scores':[{'profile':p,'score':.85} for p in request().profiles],
        'evidence':['The isolated mechanical cuff has the expected circular structure and blue emitter.'],
        'proposed_decision':'pass','summary':'Core object structure is usable.'}
    return VisionInspectionDraft.model_validate({**payload,**changes})


def issue(aspect='glow_intensity',severity='minor'):
    return {'issue_type':'lighting_mismatch','severity':severity,'message':'Blue light is brighter than preferred',
        'evidence':['Blue light is visible around the cuff emitter.'],'reference_aspect':aspect,
        'observed_subject':'cuff emitter','observed_mismatch':'blue light is brighter than preferred',
        'blocking_reason':'Soft decorative intensity variation only','core_usability_affected':False}


def test_prop_glow_warning_does_not_block_but_critical_is_invalid():
    p=ReferenceAcceptancePolicy();r=request()
    assert p.decide(r,draft(issues=[issue()]))[0]==VisionDecision.PASS
    with pytest.raises(ValueError,match='soft reference detail'):
        p.decide(r,draft(issues=[issue(severity='critical')],proposed_decision='human_review'))
    with pytest.raises(ValueError,match='Prop illumination'):
        p.decide(r,draft(issues=[{**issue('defining_design','critical'),'core_usability_affected':True}],proposed_decision='human_review'))


@pytest.mark.parametrize('role',['character','location','prop','style'])
def test_scoped_requirements_cannot_import_other_subjects(role):
    requirements=scoped_reference_requirements(role,'ONLY_CANONICAL_TARGET')
    assert requirements[0]=='ONLY_CANONICAL_TARGET'
    assert 'NOT required' in requirements[1]
    assert 'OTHER_CHARACTER_NAME' not in str(requirements)


def test_real_core_blocker_remains_blocking_and_requires_evidence():
    p=ReferenceAcceptancePolicy();r=request('character')
    wrong={**issue('wrong_person','critical'),'core_usability_affected':True,'issue_type':'identity_drift',
           'observed_subject':'the main visible person','observed_mismatch':'A different person with incompatible defining face',
           'blocking_reason':'Cannot anchor the canonical recurring character','suggested_action':'regenerate_first_frame'}
    d=draft(issues=[wrong],proposed_decision='repair')
    assert p.decide(r,d)[0]!=VisionDecision.PASS
    with pytest.raises(ValueError,match='observable evidence'):
        p.decide(r,draft(issues=[{**wrong,'evidence':[]}],proposed_decision='repair'))


def authorize(store):
    store.create_structured('p5r_recovery_authorization',{'policy_revisions':[REVISION],
        'recovery_authorization':AUTHORIZATION,'critic_config_revision':CRITIC_REVISION})


def test_invalid_critic_spends_provider_budget_not_material_and_history_survives(tmp_path):
    store=LocalArtifactStore(tmp_path);ledger=QualityBudgetLedger(store);r=request()
    for n in range(3):ledger.reserve(WorkKind.INSPECT,'same',inputs=n,config={})
    with pytest.raises(BudgetExhausted,match='authorization'):ledger.ensure_inspection_available('same',r)
    authorize(store)
    for n in range(3):
        record=ledger.reserve_critic(r,f'new-policy-{n}')
        ledger.finish_critic(record,error=ValueError('missing evidence'))
    resumed=QualityBudgetLedger(store)
    assert len(resumed.records())==3
    assert not any(o['material_quality_consumed'] for o in resumed.critic_outcomes())
    with pytest.raises(BudgetExhausted,match='Critic/provider'):resumed.ensure_inspection_available('same',r)


def test_only_committed_nonpass_consumes_material(tmp_path):
    store=LocalArtifactStore(tmp_path);authorize(store);ledger=QualityBudgetLedger(store);r=request()
    for n in range(2):
        row=ledger.reserve_critic(r,str(n))
        ledger.finish_critic(row,result=NS(decision=VisionDecision.HUMAN_REVIEW,result_id=str(n),provider_metadata={},
            issues=[NS(severity=NS(value='major'))]))
    with pytest.raises(BudgetExhausted,match='Material quality'):ledger.ensure_inspection_available('same',r)


def test_config_fix_retains_history_and_has_finite_total_budget(tmp_path):
    store=LocalArtifactStore(tmp_path);ledger=QualityBudgetLedger(store)
    old=request().model_copy(update={'critic_config_revision':'old'})
    store.create_structured('p5r_recovery_authorization',{'policy_revisions':[REVISION],
        'recovery_authorization':AUTHORIZATION,'critic_config_revision':'old'})
    for n in range(3):ledger.finish_critic(ledger.reserve_critic(old,str(n)),error=ValueError('invalid'))
    with pytest.raises(BudgetExhausted,match='authorization'):ledger.ensure_inspection_available('same',request())
    authorize(store)
    for n in range(3,6):ledger.finish_critic(ledger.reserve_critic(request(),str(n)),error=ValueError('invalid'))
    assert len(ledger.critic_records())==6
    assert not any(o['material_quality_consumed'] for o in ledger.critic_outcomes())
    with pytest.raises(BudgetExhausted,match='Critic/provider'):ledger.ensure_inspection_available('same',request())


def test_material_budget_amendment_is_scoped_and_does_not_reset_history(tmp_path):
    store=LocalArtifactStore(tmp_path);authorize(store);ledger=QualityBudgetLedger(store)
    for subject in ('identity_CHAR_LYNNE_001','other'):
        r=request().model_copy(update={'image_artifact_id':subject})
        for n in range(2):
            row=ledger.reserve_critic(r,subject+str(n))
            ledger.finish_critic(row,result=NS(decision=VisionDecision.HUMAN_REVIEW,result_id=str(n),
                provider_metadata={},issues=[NS(severity=NS(value='major'))]))
    previous=store.read_structured(store.get('p5r_recovery_authorization'))
    store.create_structured('p5r_recovery_authorization',{**previous,'material_failure_overrides':{'identity_CHAR_LYNNE_001':3}})
    ledger.ensure_inspection_available('identity_CHAR_LYNNE_001',request().model_copy(update={'image_artifact_id':'identity_CHAR_LYNNE_001'}))
    with pytest.raises(BudgetExhausted,match='Material quality'):
        ledger.ensure_inspection_available('other',request().model_copy(update={'image_artifact_id':'other'}))
    assert len(ledger.critic_outcomes())==4


def test_inconclusive_committed_review_is_not_a_proven_material_failure(tmp_path):
    store=LocalArtifactStore(tmp_path);authorize(store);ledger=QualityBudgetLedger(store)
    row=ledger.reserve_critic(request(),'uncertain-diagnosis')
    ledger.finish_critic(row,result=NS(decision=VisionDecision.HUMAN_REVIEW,result_id='r',provider_metadata={},issues=[]))
    assert ledger.critic_outcomes()[0]['outcome']=='committed_inconclusive'
    assert not ledger.critic_outcomes()[0]['material_quality_consumed']


def test_policy_revision_busts_cache_without_changing_artifact_identity():
    r=request();old=r.model_copy(update={'quality_policy_revision':None,'recovery_authorization':None})
    assert r.image_artifact_id==old.image_artifact_id
    assert inspection_fingerprint(r)!=inspection_fingerprint(old)


def test_p5r_schema_cannot_omit_observable_issue_evidence():
    from movie_agent.quality.vision import inspection_draft_schema
    schema=inspection_draft_schema(request())
    issue=schema['$defs']['DraftIssue']
    assert {'evidence','observed_subject','observed_mismatch','blocking_reason','core_usability_affected'}<=set(issue['required'])
    assert issue['properties']['evidence']['minItems']==1
    assert schema['properties']['evidence']['minItems']==1
    old=inspection_draft_schema(request().model_copy(update={'quality_policy_revision':None}))
    assert 'evidence' not in old['$defs']['DraftIssue']['required']


def test_style_is_soft_visible_prop_is_hard():
    s=shot();scene=NS(location_id='loc');bible=NS(visual_bible_id='style')
    pack=ReferenceIdentitySet(references=[anchor(ReferenceType.CHARACTER,'c',ReferenceRole.PORTRAIT),
        anchor(ReferenceType.LOCATION,'loc',ReferenceRole.ENVIRONMENT)])
    resolved=ReferenceResolver().resolve(s,scene,bible,pack)
    assert len(resolved.hard_required_refs)==2 and resolved.missing_soft_refs==['style:style']
    from movie_agent.domain import PropState
    s.state_before.prop_states['device']=PropState(prop_id='device',visible=True)
    with pytest.raises(ValueError,match='prop'):ReferenceResolver().resolve(s,scene,bible,pack)
    s.state_before.prop_states['device'].visible=False
    assert len(ReferenceResolver().resolve(s,scene,bible,pack).hard_required_refs)==2


def test_structural_removal_or_failed_edit_routes_to_flux():
    policy=RepairCostPolicy()
    assert policy.route_image(structural=True).kind==WorkKind.FRAME
    assert policy.route_image(structural=False,previous_semantic_edit_failed=True).kind==WorkKind.FRAME
    assert policy.route_image(structural=False).kind==WorkKind.FRAME_EDIT
    assert policy.route_image(structural=False,minor_nonblocking=True) is None
