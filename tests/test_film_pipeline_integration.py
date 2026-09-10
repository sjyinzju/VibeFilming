"""Offline formal-DAG integration: synthetic video, HTTP audio doubles, real FFmpeg/export/download.

This checks workflow wiring and durability, never AI visual fidelity or live H3 acceptance.
"""
import json
import shutil
import subprocess
from hashlib import sha256
import httpx
import pytest
from movie_agent.domain import ProviderResult,QualityProfile,AspectRatio
from movie_agent.providers.media import MockVideoProvider,BinaryPayload,ProviderMediaResponse
from movie_agent.providers.registry import MediaProviderSettings
from movie_agent.services.film_production import FilmProduction
from movie_agent.quality.production import FilmProductionPolicy
from movie_agent.application.service import ProductionService
from movie_agent.storage.projects import LocalProjectRepository
from movie_agent.api.app import create_app
from tests.p2a_fakes import FakeReasoningProvider
from tests.test_p2a_runtime import brief
from tests.test_p4d_audio import wav_bytes


class PipelineReasoning(FakeReasoningProvider):
    shot_count=1
    async def submit(self,request):
        name=request.generation_request.parameters['response_format']['json_schema']['name']
        if name=='cinematic_proposal':
            self.calls.append(name)
            context=json.loads(request.generation_request.prompt_package.positive_prompt)
            evidence=context.get('evidence_ids') or [i['result_id'] for i in context['observed_evidence']]
            return ProviderResult(provider_request_id=request.provider_request_id,success=True,metadata={
                'finish_reason':'stop','content':json.dumps({'score':.95,'summary':'Controlled integration evidence',
                    'evidence_ids':evidence,'action':'KEEP','rationale':'Fixture observations support this cut',
                    'major_continuity_break':False,'dimensions':{k:.95 for k in ('composition','visual_hierarchy',
                        'emotional_clarity','performance_readability','shot_motivation','continuity','editing_usefulness')}})})
        result=await super().submit(request)
        if name=='ShotPlanDraft':
            data=json.loads(result.metadata['content']);data['shots'][0]['narrative']['dialogue']=['Person: Hello.']
            data['shots'][0]['performances']=[{'character_id':'person','action':'Listens to the signal'}]
            if self.shot_count>1:
                duration=data['shots'][0]['duration_seconds']/self.shot_count
                data['shots']=[{**data['shots'][0],'duration_seconds':duration} for _ in range(self.shot_count)]
            result.metadata['content']=json.dumps(data)
        return result


class SyntheticVideo(MockVideoProvider):
    def __init__(self,root):super().__init__();self.root=root;self.calls=0
    async def capabilities(self):
        capabilities=await super().capabilities()
        capabilities.video.multi_reference=False
        return capabilities
    async def generate(self,request,**kwargs):
        self.calls+=1
        response=await super().generate(request,**kwargs)
        path=self.root/(request.request_id+'.mp4')
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
            f'testsrc2=size={request.width}x{request.height}:rate={request.fps}:duration={request.duration_seconds}',
            '-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True,capture_output=True)
        # As in the post tests, this is real encoded local media from a deterministic
        # source, not the mock provider's undecodable placeholder payload.
        result=response.result.model_copy(update={'provider_id':'ffmpeg-fixture',
            'provider_metadata':{'mock':False,'synthetic_source':'ffmpeg test pattern'}})
        return ProviderMediaResponse(result,(BinaryPayload(request.output_artifact_id,path.read_bytes(),'video/mp4','mp4','shot_video'),))


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='FFmpeg required')
@pytest.mark.asyncio
@pytest.mark.parametrize('change_reference,omit_one,aspect',[(False,False,'16:9'),(True,False,'16:9'),
    (False,True,'16:9'),(False,False,'9:16')])
async def test_new_project_reaches_downloadable_candidate_through_formal_dag(tmp_path,change_reference,omit_one,aspect):
    model=PipelineReasoning();audio_calls=[]
    if omit_one:model.shot_count=5
    def audio_transport(request):
        path=request.url.path
        if path=='/health':return httpx.Response(200,json={'ready':True,'data':{'status':'ok','models_initialized':True}})
        audio_calls.append(path)
        if path=='/release_task':return httpx.Response(200,json={'data':{'task_id':'fixture_music'}})
        if path=='/query_result':return httpx.Response(200,json={'data':[{'task_id':'fixture_music','status':1,
            'result':json.dumps([{'file':'/v1/audio?path=music.wav'}])}]})
        return httpx.Response(200,content=wav_bytes(duration=20 if path=='/v1/audio' else .5,
            channels=2 if path=='/v1/audio' else 1),headers={'Content-Type':'audio/wav'})
    def factory(pid):
        workspace=tmp_path/pid
        settings=MediaProviderSettings(audio_provider='real',post_provider='ffmpeg',
            audio_task_state_root=str(workspace/'tasks'),post_temp_root=str(workspace/'temp'))
        from movie_agent.model_services.wiring import build_spark_runtime
        from movie_agent.model_services.resources import ResourceRuntimeSettings
        from movie_agent.config import LLMConfig
        runtime=build_spark_runtime(LLMConfig(base_url='http://127.0.0.1:8000/v1',model='fixture'),settings,
            settings=ResourceRuntimeSettings(enabled=False,state_path=str(workspace/'runtime.json')))
        engine=FilmProduction(workspace,model,media_settings=settings,runtime_coordinator=runtime)
        engine.media_runtime.providers._providers['mock-video']=SyntheticVideo(workspace)
        if omit_one:
            original_gate=engine.gate_frames
            async def controlled_gate(project,shot):
                # A deterministic rejected-content fixture exercises the real downstream DAG.
                if shot.shot_id.endswith('SHOT-02'):
                    engine.commit_quality('frame_gate_'+shot.shot_id,{'passed':False,'shot_id':shot.shot_id},'frame_gate')
                    return False
                return await original_gate(project,shot)
            engine.gate_frames=controlled_gate
        for name in ('qwen3_tts','ace_step'):engine.media_runtime.providers.get(name).transport=httpx.MockTransport(audio_transport)
        return engine
    service=ProductionService(LocalProjectRepository(tmp_path),factory)
    spec=brief().model_copy(update={'resolution':'90x160' if aspect=='9:16' else '160x90','aspect_ratio':AspectRatio(aspect),
        'quality_level':QualityProfile.DRAFT,
        'target_duration':20 if omit_one else 2,'max_shots':5 if omit_one else 3})
    record=service.create(spec);pid=record.project.project_id;engine=service.engine(pid)
    engine.initialize_project(engine.current_project,engine.current_production,FilmProductionPolicy(require_real_providers=False))
    service.start(pid);await service.tasks[pid]
    assert service.repository.get(pid).failure_code is None
    candidate=engine.artifact_store.get('technical_candidate_final')
    assert candidate is not None, {'failure':service.repository.get(pid).failure_code,
        'reviews':[(r.node_id,r.question) for r in engine.human_gates.all()]}
    assert candidate.metadata['export_intent']=='candidate' and candidate.metadata['qc']['passed']
    assert candidate.metadata['human_aesthetically_approved'] is False
    for artifact in engine.artifact_store.list_all():
        if artifact.artifact_id.startswith('frame_') and artifact.metadata.get('width'):
            width,height=map(int,aspect.split(':'))
            assert artifact.metadata['width']*height==artifact.metadata['height']*width
    assert engine.current_production.node('final_gate').status.value=='waiting_human'
    assert engine.current_production.node('full_film_review').status.value=='succeeded'
    assert engine.media_runtime.providers.get('mock-video').calls==(4 if omit_one else 1)
    if omit_one:
        assert len(candidate.metadata['render_plan']['segments'])==4
        assert engine.current_production.node('production_frame_gate:scene_a-SHOT-02').status.value=='waiting_human'
    assert 'cinematic_proposal' in model.calls and audio_calls
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service)),base_url='http://test') as client:
        response=await client.get(f'/projects/{pid}/artifacts/{candidate.artifact_id}/versions/{candidate.version}/download')
        assert response.status_code==200 and sha256(response.content).hexdigest()==candidate.metadata['sha256']
    calls=len(model.calls),len(audio_calls)
    service.start(pid,resume=True);await service.tasks[pid]
    assert (len(model.calls),len(audio_calls))==calls
    assert engine.artifact_store.get(candidate.artifact_id).metadata['sha256']==candidate.metadata['sha256']
    if change_reference:
        from movie_agent.application.reference_commands import accept_references
        from movie_agent.quality.recovery import HumanReferenceAcceptance,AcceptedReferenceBinding
        character=engine.current_project.characters[0]
        selected=next(r for r in engine.pack().references if r.subject_id==character.character_id and r.selected)
        command=HumanReferenceAcceptance(acceptance_id='new_face_selection',workflow_node_id='explicit_face_review',
            user_statement='Accept this exact image for face identity in the regression fixture.',question='Accept this face?',
            bindings=[AcceptedReferenceBinding(subject_id=character.character_id,role='face_identity',
                reference=selected.media_reference())])
        old_gate=engine.artifact_store.get('frame_gate_'+engine.current_project.shots[0].shot_id)
        old_fingerprint=engine.artifact_store.read_structured(old_gate)['reference_fingerprint']
        accept_references(service,pid,command)
        service.start(pid,resume=True);await service.tasks[pid]
        assert service.repository.get(pid).failure_code is None
        current=engine.artifact_store.get(candidate.artifact_id)
        assert current.version==candidate.version+1,[(r.node_id,r.question) for r in engine.human_gates.all()]
        gate=engine.artifact_store.get(old_gate.artifact_id)
        assert engine.artifact_store.read_structured(gate)['reference_fingerprint']!=old_fingerprint
        old_frames=engine.artifact_store.read_structured(old_gate)['frames']
        new_frames=engine.artifact_store.read_structured(gate)['frames']
        assert all(new['version']>old['version'] for new,old in zip(new_frames,old_frames,strict=True))
        assert engine.media_runtime.providers.get('mock-video').calls==2
        assert len(audio_calls)==calls[1]
        assert engine.artifact_store.get(candidate.artifact_id,candidate.version).metadata['sha256']==candidate.metadata['sha256']
    if not change_reference and not omit_one:
        review=engine.human_gates.pending_for_node('final_gate')
        service.resolve_review(review.review_id,True,'Explicit approval in the offline integration fixture.')
        service.start(pid,resume=True);await service.tasks[pid]
        assert service.repository.get(pid).failure_code is None
        final=engine.artifact_store.get('final_film')
        assert final and final.metadata['export_intent']=='approved_final'
        assert engine.artifact_store.get(candidate.artifact_id,candidate.version).metadata['sha256']==candidate.metadata['sha256']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service)),base_url='http://test') as client:
            response=await client.get(f'/projects/{pid}/exports/final')
            assert response.status_code==200 and sha256(response.content).hexdigest()==final.metadata['sha256']
    await service.shutdown()
    engine.media_runtime.runtime_coordinator.close()
