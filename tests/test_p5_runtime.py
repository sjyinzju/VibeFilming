import io
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from PIL import Image

from movie_agent.domain import PromptPackage,GenerationJob,Project,ProjectBrief,QualityProfile
from movie_agent.media import ImageGenerationRequest,ImageGenerationMode,MediaReference,ReferenceType
from movie_agent.providers.flux_kontext import FluxKontextImageProvider
from movie_agent.providers.base import ProviderFailure


@pytest.mark.asyncio
async def test_kontext_provider_pins_and_provenance():
    out=io.BytesIO();Image.new('RGB',(256,256),'gray').save(out,format='PNG');data=out.getvalue();digest=sha256(data).hexdigest()
    def handler(request):
        return httpx.Response(200,content=data,headers={'content-type':'image/png','x-kontext-sha256':digest,
            'x-kontext-source-sha256':digest,'x-kontext-seed':'42','x-kontext-inference-seconds':'1.5'})
    resolver=SimpleNamespace(resolve=lambda ref:SimpleNamespace(content=data))
    provider=FluxKontextImageProvider(endpoint='http://test',resolver=resolver,transport=httpx.MockTransport(handler))
    request=ImageGenerationRequest(job_id='j',project_id='p',mode=ImageGenerationMode.IMAGE_EDIT,purpose='first_frame',
        source_image=MediaReference(reference_type=ReferenceType.SOURCE_IMAGE,artifact_id='source',version=3,sha256=digest),
        prompt_package=PromptPackage(compiler_id='test',compiler_version='1',positive_prompt='warmer lighting'),
        output_artifact_id='edit',width=256,height=256,aspect_ratio='1:1',provider_parameters={'preserve':['identity'],'change':['lighting']})
    result=await provider.generate(request)
    p=result.result.provenance.parameters
    assert p['mock'] is False and p['source_version']==3 and p['sha256']==digest and p['preserve']==['identity']
    caps=await provider.capabilities()
    assert caps.image.image_edit and not caps.image.multi_reference and not caps.image.inpaint
    with pytest.raises(ProviderFailure): await provider.generate(request.model_copy(update={'source_image':request.source_image.model_copy(update={'version':None})}))
    with pytest.raises(ProviderFailure): await provider.generate(request.model_copy(update={'mask_artifact_id':'mask'}))
    from movie_agent.domain import ProviderErrorType
    for status,expected in [(422,ProviderErrorType.INVALID_REQUEST),(500,ProviderErrorType.GENERATION_FAILED)]:
        provider.transport=httpx.MockTransport(lambda req:httpx.Response(status,json={'error':'rejected'}))
        with pytest.raises(ProviderFailure) as failure:await provider.generate(request)
        assert failure.value.error_type==expected and not failure.value.retryable


@pytest.mark.asyncio
async def test_still_inspection_has_one_frame_and_no_invented_timeline():
    from movie_agent.providers.qwen3_vl import Qwen3VLVisionProvider,SYSTEM_PROMPT
    from movie_agent.providers.registry import MediaProviderSettings
    from movie_agent.media import VisionInspectionRequest
    from movie_agent.quality.vision import VisionInspectionDraft,validate_semantics
    provider=Qwen3VLVisionProvider(settings=MediaProviderSettings(),resolver=None)
    request=await provider.prepare_request(VisionInspectionRequest(job_id='j',project_id='p',
        image_artifact_id='image',output_artifact_id='inspection',profiles=['image_quality']))
    assert request.source_frame_count==1 and request.source_duration_seconds is None
    assert 'time_ranges must be []' in SYSTEM_PROMPT
    data={'scores':[{'profile':'image_quality','score':.9}], 'proposed_decision':'repair',
          'summary':'Visible lighting mismatch','evidence':['Amber background'],
          'issues':[{'issue_type':'lighting_mismatch','severity':'major','message':'Amber instead of blue',
                     'evidence':['Amber background'],'frame_references':[{'frame_number':0}]}]}
    validate_semantics(request,VisionInspectionDraft.model_validate(data))
    data['issues'][0]['time_ranges']=[{'start_seconds':0,'end_seconds':1}]
    with pytest.raises(ValueError,match='source duration'):
        validate_semantics(request,VisionInspectionDraft.model_validate(data))


@pytest.mark.asyncio
async def test_frame_gate_prevents_h3_even_on_direct_repair_path(tmp_path):
    from movie_agent.services.hero_production import HeroMovieProduction
    from movie_agent.providers.registry import MediaProviderSettings
    from tests.p2a_fakes import FakeReasoningProvider
    from tests.test_p5_quality import shot
    engine=HeroMovieProduction(tmp_path,FakeReasoningProvider(),media_settings=MediaProviderSettings())
    project=Project(brief=ProjectBrief(title='t',logline='t',story_description='t',target_duration=90),shots=[shot()])
    with pytest.raises(ValueError,match='frame gate'): await engine._generate_versions(project,project.shots)
    assert not engine.media_runtime.quality_ledger.records()


@pytest.mark.asyncio
@pytest.mark.parametrize('quality',[QualityProfile.DRAFT,QualityProfile.STANDARD,QualityProfile.SHOWCASE])
async def test_candidate_requires_passing_exact_shots_and_preserves_gate(tmp_path,quality):
    from tests.test_p4c_post import fixture
    from movie_agent.services.post_production import technical_candidate
    from movie_agent.domain import HumanGateType
    engine,request=fixture(tmp_path)
    project=engine.current_project
    project.brief.quality_level=quality
    request=request.model_copy(update={'quality_profile':quality})
    rough=await engine.media_runtime.post_process(GenerationJob(job_id='rough',project_id=project.project_id,
        task='post',idempotency_key='rough'),request.model_copy(update={'output_artifact_id':'rough_cut'}))
    review=engine.human_gates.request(project.project_id,'final_gate',HumanGateType.FINAL_CUT_APPROVAL,'review',['rough_cut'],target_artifact=rough)
    timeline=request.timeline
    engine.artifact_store.create_structured('film_quality_report',{'shots':[{'shot_id':c.shot_id,'status':'accepted',
        'blocking_issue_ids':[],'artifact_id':c.artifact_id,'artifact_version':c.version,'sha256':c.sha256}
        for t in timeline.video_tracks for c in t.clips]})
    candidate=await technical_candidate(engine,project)
    assert candidate.metadata['mock'] is False and candidate.metadata['human_aesthetically_approved'] is False
    assert engine.human_gates.get(review.review_id).status.value=='pending'
    engine.artifact_store.create_structured('film_quality_report',{'shots':[]})
    with pytest.raises(ValueError,match='passing quality'): await technical_candidate(engine,project)
