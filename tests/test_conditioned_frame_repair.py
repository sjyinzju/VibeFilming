from types import SimpleNamespace as NS
import pytest
from movie_agent.media import MediaReference, ReferenceType, ImageCapabilities, VisionDecision as D
from movie_agent.quality.budget import RepairCostPolicy, WorkKind
from tests.test_p5r_progress import engine


def test_identity_locked_repair_never_routes_to_unconditioned_generation():
    policy=RepairCostPolicy()
    assert policy.route_image(structural=True,preserve_reference_identity=True) is None
    assert policy.route_image(structural=True,preserve_reference_identity=True,
        reference_conditioning_available=True).kind==WorkKind.FRAME_EDIT
    assert policy.route_image(structural=True).kind==WorkKind.FRAME


@pytest.mark.asyncio
@pytest.mark.parametrize('already_repaired',[False,True])
async def test_frame_repair_conditions_all_references_and_resume_does_not_repeat(tmp_path,already_repaired):
    e=engine(tmp_path);shot=e.current_project.shots[0];calls=[];inspected=[]
    store=e.artifact_store
    first=store.create_structured('first',{});last=store.create_structured('last',{})
    face=store.create_structured('face',{});body=store.create_structured('body',{})
    e.pin=lambda a,kind=ReferenceType.SOURCE_IMAGE:MediaReference(reference_type=kind,
        artifact_id=a.artifact_id,version=a.version,sha256=a.metadata['sha256'])
    refs=[e.pin(a,ReferenceType.CHARACTER).model_copy(update={'semantic_role':role})
        for a,role in ((face,'portrait'),(body,'wardrobe'))]
    e.shot_references=lambda *args,**kwargs:refs;e._select=lambda *args:None
    e.commit_quality('p5r_frame_pair_'+shot.shot_id,{'first':e.pin(first).model_dump(mode='json'),
        'last':e.pin(last).model_dump(mode='json')},'pair')
    if already_repaired:
        store.create_structured('first',{'repair':True})
        store.create_structured('p5r_frame_repair_'+shot.shot_id+'_first',{'old_strategy':True})
    async def capabilities():return [NS(image=ImageCapabilities(reference_sheet=True,max_reference_images=6))]
    e.media_runtime.capabilities=capabilities
    async def inspect(project,artifact,**kwargs):
        inspected.append((artifact.artifact_id,artifact.version))
        rejected=already_repaired or (artifact.artifact_id=='first' and artifact.version==1)
        return NS(result_id='inspection_'+str(len(inspected)),decision=D.HUMAN_REVIEW if rejected else D.PASS,
            issues=[NS(severity=NS(value='major'),issue_type=NS(value='prop_drift'),message='Required handheld prop missing')] if rejected else [])
    async def image(project,identity,prompt,**kwargs):
        calls.append(kwargs);return store.create_structured(identity,{'repair':True})
    e.inspect_frame=inspect;e.image=image
    passed=await e.gate_frames(e.current_project,shot)
    if already_repaired:
        assert not passed and calls==[] and inspected==[('first',2)]
    else:
        assert passed and len(calls)==1
        assert calls[0]['source'] is None and calls[0]['conditioning']=='reference_sheet'
        assert [r.semantic_role for r in calls[0]['references']]==['repair_base','portrait','wardrobe']
