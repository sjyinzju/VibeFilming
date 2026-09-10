import io
import json
import base64
from hashlib import sha256
from types import SimpleNamespace as NS
import httpx
import pytest
from PIL import Image
from movie_agent.artifacts import LocalArtifactStore
from movie_agent.domain import Project, ProjectBrief, WorkflowGraph, WorkflowNode, WorkflowNodeStatus as S, HumanGateType
from movie_agent.orchestration.graph import ProductionGraph
from movie_agent.orchestration.human_gate import HumanGateManager
from movie_agent.execution.events import LocalEventBus
from movie_agent.media import MediaReference, ReferenceType, ImageGenerationRequest, ImageGenerationMode
from movie_agent.quality.references import IdentityReference, ReferenceIdentitySet, ReferenceResolver, ReferenceRole
from movie_agent.quality.recovery import RecoveryPlan, ReferenceRecoveryAction, HumanReferenceAcceptance, AcceptedReferenceBinding
from movie_agent.services.recovery_authorization import accept_reference_bundle
from movie_agent.providers.flux_kontext import FluxKontextImageProvider
from tests.test_p5_quality import shot


def acceptance_engine(tmp_path,subject):
    store=LocalArtifactStore(tmp_path)
    for content in ('face','wardrobe'):store.create_structured('character_asset',{'content':content})
    location=store.create_structured('location_asset',{})
    store.create_structured('negative_original',{'decision':'human_review','issue':'Full image is not usable alone'})
    store.create_structured('reference_identity_set',ReferenceIdentitySet(references=[IdentityReference(
        artifact_id='location_asset',version=1,sha256=location.metadata['sha256'],reference_type='location',reference_role='environment',
        subject_id='place',selected=True,quality_status='pass',inspection_result_id='location-inspection')]).model_dump(mode='json'))
    plan=RecoveryPlan(plan_revision='arbitrary',reference_gate_nodes={subject:'reference_original'},reference_actions=[
        ReferenceRecoveryAction(node_id='reference_original',subject_id=subject,reference_type='character',artifact_id='character_asset',
            purpose='character_plate',strategy='inspect_existing')])
    e=NS(artifact_store=store,current_project=Project(brief=ProjectBrief(title='unrelated project',logline='x',story_description='x',target_duration=30)),
        current_production=ProductionGraph(WorkflowGraph(project_id='p',nodes=[WorkflowNode(node_id=n,node_type='test',label=n,
            role='core',group='test',status=S.SUCCEEDED if n=='shot_gate' else S.WAITING_HUMAN) for n in ('shot_gate','reference_original')])),
        human_gates=HumanGateManager(LocalEventBus(),'trace'),_durable_media_checkpoint=lambda:None)
    e.recovery_plan=lambda:plan
    e.configure_recovery=lambda new:None
    e.commit_quality=lambda identity,content,purpose:store.create_structured(identity,content,metadata={'purpose':purpose})
    e.pin=lambda a:NS(sha256=a.metadata['sha256'])
    e.pack=lambda:ReferenceIdentitySet.model_validate(store.read_structured(store.get('reference_identity_set')))
    return e


@pytest.mark.parametrize('subject',['pilot_alpha','unrelated_person_72'])
def test_human_acceptance_composes_exact_roles_for_arbitrary_entities(tmp_path,subject):
    e=acceptance_engine(tmp_path,subject)
    old=e.human_gates.request(e.current_project.project_id,'reference_original',HumanGateType.AGENT_ESCALATION,'Not usable alone')
    command=HumanReferenceAcceptance(acceptance_id='accepted_bundle',workflow_node_id='manual_bundle_gate',
        user_statement='I accept these exact complementary images.',question='Accept face plus wardrobe?',superseded_review_ids=[old.review_id],
        bindings=[AcceptedReferenceBinding(subject_id=subject,role=role,known_limitations=[limitation],
            reference=MediaReference(reference_type='character',artifact_id='character_asset',version=version,
                sha256=e.artifact_store.get('character_asset',version).metadata['sha256'],entity_id=subject))
            for role,version,digest,limitation in [('portrait',1,'a'*64,'Face only; body not established'),
                ('wardrobe',2,'b'*64,'Clothing only; face not established')]])
    before=e.artifact_store.read_structured(e.artifact_store.get('negative_original'))
    collision=command.model_copy(update={'workflow_node_id':'reference_original'})
    with pytest.raises(ValueError,match='another workflow gate'):accept_reference_bundle(e,collision)
    assert e.human_gates.get(old.review_id).superseded_at is None
    accept_reference_bundle(e,command);accept_reference_bundle(e,command)
    assert e.current_production.node('manual_bundle_gate').status==S.SUCCEEDED
    assert e.human_gates.get(old.review_id).status.value=='pending'
    assert e.human_gates.get(old.review_id).superseded_at is not None
    assert e.artifact_store.read_structured(e.artifact_store.get('negative_original'))==before
    s=shot();state=next(iter(s.state_before.character_states.values()))
    s.state_before.character_states={subject:state.model_copy(update={'character_id':subject})}
    s.expected_state_after.character_states={subject:state.model_copy(update={'character_id':subject})};s.performances=[]
    refs=ReferenceResolver().resolve(s,NS(location_id='place'),None,e.pack(),store=e.artifact_store)
    assert [(r.semantic_role,r.version) for r in refs.references if r.reference_type==ReferenceType.CHARACTER]==[('portrait',1),('wardrobe',2)]
    assert all(r.human_acceptance_artifact_id=='accepted_bundle' for r in refs.references[:2])
    with pytest.raises(ValueError,match='reference bound'):ReferenceResolver(max_references=2).resolve(s,NS(location_id='place'),None,e.pack())
    altered=command.model_copy(deep=True);altered.bindings[0].known_limitations=['A different approval scope']
    with pytest.raises(ValueError,match='cannot be reused'):accept_reference_bundle(e,altered)
    pack=e.pack();pack.references[-1].sha256='a'*64
    with pytest.raises(ValueError,match='version/hash'):ReferenceResolver().resolve(s,NS(location_id='place'),None,pack,store=e.artifact_store)


@pytest.mark.asyncio
async def test_human_accepted_reference_is_not_sent_to_another_vlm_gate(tmp_path):
    from movie_agent.services.reference_recovery import ReferenceRecoveryProduction
    e=acceptance_engine(tmp_path,'another_person')
    e.pack=lambda:NS(references=[NS(subject_id='another_person',selected=True,human_review_id='approved_review')])
    async def forbidden(*args,**kwargs):
        pytest.fail('Human-accepted baseline must not be regenerated or reinspected')
    e.image=forbidden;e.inspect_frame=forbidden
    action=e.recovery_plan().reference_actions[0]
    assert await ReferenceRecoveryProduction.recover_reference(e,e.current_project,action)


def png(color):
    out=io.BytesIO();Image.new('RGB',(256,256),color).save(out,format='PNG');return out.getvalue()


def test_same_image_keeps_all_roles_and_validates_each_acceptance(tmp_path):
    e=acceptance_engine(tmp_path,'c')
    pack=e.pack(); image=e.artifact_store.get('character_asset',1)
    for role in ('portrait','wardrobe','silhouette'):
        pack.references.append(IdentityReference(artifact_id=image.artifact_id,version=1,sha256=image.metadata['sha256'],
            reference_type='character',reference_role=role,subject_id='c',selected=True,
            quality_status='pass',inspection_result_id='baseline'))
    s=shot();state=next(iter(s.state_before.character_states.values())).model_copy(update={'character_id':'c'})
    s.state_before.character_states={'c':state};s.expected_state_after.character_states={'c':state};s.performances=[]
    resolved=ReferenceResolver(max_references=2).resolve(s,NS(location_id='place'),None,pack,store=e.artifact_store)
    assert len(resolved.references)==2
    assert resolved.references[0].semantic_role=='portrait + wardrobe + silhouette'
    assert resolved.hard_required_refs[0].semantic_role==resolved.references[0].semantic_role
    pack.references[-1].human_review_id='unverified';pack.references[-1].acceptance_artifact_id='missing_approval'
    with pytest.raises(ValueError,match='acceptance does not cover'):
        ReferenceResolver().resolve(s,NS(location_id='place'),None,pack,store=e.artifact_store)


@pytest.mark.asyncio
async def test_provider_receives_every_semantic_reference_as_actual_image_bytes():
    images={'face':png('red'),'clothes':png('blue')};observed={};result_png=png('gray')
    refs=[MediaReference(reference_type='character',artifact_id=identity,version=1,sha256=sha256(content).hexdigest(),
        semantic_role='portrait' if identity=='face' else 'wardrobe',entity_id='any-character') for identity,content in images.items()]
    def handler(request):
        body=json.loads(request.content);content=base64.b64decode(body['source_base64']);observed['source']=content
        observed['prompt']=body['prompt']
        assert sha256(content).hexdigest()==body['source_sha256']
        return httpx.Response(200,content=result_png,headers={'content-type':'image/png','x-kontext-sha256':sha256(result_png).hexdigest(),
            'x-kontext-source-sha256':body['source_sha256'],'x-kontext-seed':'42','x-kontext-inference-seconds':'1.0'})
    resolver=NS(resolve=lambda ref:NS(content=images[ref.artifact_id]))
    provider=FluxKontextImageProvider(endpoint='http://test',resolver=resolver,transport=httpx.MockTransport(handler))
    from movie_agent.domain import PromptPackage
    request=ImageGenerationRequest(job_id='j',project_id='arbitrary',mode=ImageGenerationMode.IMAGE_EDIT,purpose='first_frame',
        references=refs,reference_conditioning='reference_sheet',output_artifact_id='frame',width=256,height=256,aspect_ratio='1:1',
        prompt_package=PromptPackage(compiler_id='test',compiler_version='1',positive_prompt='The person is seated in a room.'))
    response=await provider.generate(request)
    with Image.open(io.BytesIO(observed['source'])) as image:
        assert image.getpixel((256,520))==(255,0,0)
        assert image.getpixel((768,520))==(0,0,255)
    evidence=response.result.provenance.parameters['reference_conditioning']
    assert len(evidence['bindings'])==2 and evidence['native_multi_reference'] is False
    assert 'portrait' in observed['prompt'] and 'wardrobe' in observed['prompt']
    from movie_agent.providers.base import ProviderFailure
    with pytest.raises(ProviderFailure):await provider.generate(request.model_copy(update={'reference_conditioning':'native'}))
