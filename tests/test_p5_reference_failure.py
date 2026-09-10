from types import SimpleNamespace as NS
import pytest
from movie_agent.services.hero_production import HeroMovieProduction
from movie_agent.artifacts import LocalArtifactStore
from movie_agent.media import MediaReference,ReferenceType,VisionDecision
from movie_agent.quality.budget import QualityBudgetLedger,WorkKind,BudgetExhausted


@pytest.mark.asyncio
async def test_one_invalid_reference_review_does_not_skip_independent_references(tmp_path):
    engine=HeroMovieProduction.__new__(HeroMovieProduction)
    engine.artifact_store=LocalArtifactStore(tmp_path)
    engine._artifact=lambda *a,**k:None
    def commit(identity,content,purpose):
        return engine.artifact_store.create_structured(identity,content,metadata={'purpose':purpose})
    engine.commit_quality=commit
    async def image(project,identity,*a,**k):return NS(artifact_id=identity,version=1)
    engine.image=image
    engine.pin=lambda artifact:MediaReference(reference_type=ReferenceType.SOURCE_IMAGE,
        artifact_id=artifact.artifact_id,version=1,sha256='a'*64)
    seen=[]
    async def inspect(project,artifact,**kwargs):
        seen.append(artifact.artifact_id)
        if len(seen)==1:raise ValueError('Completed critic proposal has no evidence')
        return NS(decision=VisionDecision.PASS,result_id='review_'+artifact.artifact_id,scores=[NS(score=.9)])
    engine.inspect_frame=inspect
    project=NS(characters=[NS(character_id=c,identity_description=c,appearance_constraints=['plain costume']) for c in ['A','B']],
        locations=[],props=[],visual_bible=NS(visual_bible_id='style',style_statement='cinematic',palette=[],lighting_rules=[],continuity_rules=[]))
    await engine._asset_planning(project)
    assert seen==['identity_A','identity_B','identity_style']
    pack=engine.artifact_store.read_structured(engine.artifact_store.get('reference_identity_set'))
    assert all(not r['selected'] and r['inspection_result_id'] is None for r in pack['references'] if r['subject_id']=='A')
    assert all(r['selected'] for r in pack['references'] if r['subject_id']=='B')
    assert engine.artifact_store.get('reference_review_error_A')


def test_inspection_preflight_does_not_consume_an_extra_reservation(tmp_path):
    ledger=QualityBudgetLedger(LocalArtifactStore(tmp_path))
    for attempt in range(3):ledger.reserve(WorkKind.INSPECT,'frame',inputs=attempt,config={},attempt=attempt)
    with pytest.raises(BudgetExhausted):ledger.ensure_inspection_available('frame')
    assert len(ledger.records())==3
    ledger.ensure_inspection_available('independent_frame')


@pytest.mark.asyncio
async def test_empty_accepted_timeline_requests_review_before_post():
    from movie_agent.domain import WorkflowNodeStatus
    engine=HeroMovieProduction.__new__(HeroMovieProduction)
    engine.reports=lambda project:[]
    requests=[]
    engine.human_gates=NS(pending_for_node=lambda node:None,request=lambda *args:requests.append(args))
    checkpoints=[]
    engine._durable_media_checkpoint=lambda:checkpoints.append(True)
    statuses=[]
    graph=NS(set_status=lambda *args,**kwargs:statuses.append((args,kwargs)))
    assert not await engine._execute_node('audio_post',NS(project_id='project'),graph,auto_approve=True)
    assert requests[0][1]=='audio_post'
    assert 'No accepted visual shots' in requests[0][3]
    assert statuses[0][0]==('audio_post',WorkflowNodeStatus.WAITING_HUMAN)
    assert checkpoints==[True]
