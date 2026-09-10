"""Offline P4D contracts, binary transports, timing, lineage and actual FFmpeg mix."""
import asyncio
from hashlib import sha256
import io
import json
import math
import struct
import wave

import httpx
import pytest
from pydantic import ValidationError
from movie_agent.domain import Character, Project, ProjectBrief
from movie_agent.media.contracts import (AudioCue, AudioPurpose, AudioTrack, CharacterVoiceProfile,
    DialogueCue, MusicGenerationRequest, SpeechGenerationRequest, VoiceDesignRequest,
    MediaReference, ReferenceType)
from movie_agent.media.compilers import GenericAudioPromptCompiler
from movie_agent.media.dialogue import extract_dialogue, check_duration, DialogueTimingReview
from movie_agent.media.post import pin_timeline
from movie_agent.providers.audio_transport import inspect_wav
from movie_agent.providers.qwen3_tts import Qwen3TTSAudioProvider
from movie_agent.providers.ace_step import AceStepMusicProvider
from movie_agent.providers.registry import MediaProviderSettings, ProviderFactory
from tests.test_p4c_post import fixture, source, request_for, render


def wav_bytes(duration=.5, channels=1, rate=24000, amplitude=2000):
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as wav:
        wav.setnchannels(channels);wav.setsampwidth(2);wav.setframerate(rate)
        wav.writeframes(b''.join(struct.pack('<h',int(amplitude*math.sin(i*.11)))*channels for i in range(int(duration*rate))))
    return buffer.getvalue()


def request(kind=VoiceDesignRequest, **kwargs):
    default=dict(job_id='audio:test',project_id='project',output_artifact_id='audio',
        prompt_package=GenericAudioPromptCompiler().compile(AudioPurpose.SPEECH,'canonical text',[]))
    if kind is VoiceDesignRequest: default.update(character_id='char',text='林恩，不要靠近地面。',design_instruction='冷静清晰')
    elif kind is MusicGenerationRequest: default.update(mood='cold ambient',duration_target_seconds=10)
    return kind(**{**default,**kwargs})


def test_profile_pins_and_cue_slot_validation():
    with pytest.raises(ValidationError):
        CharacterVoiceProfile(voice_profile_id='voice',project_id='p',character_id='c',
            design_instruction='calm',reference_artifact_id='anchor',reference_artifact_version=0,
            reference_sha256='not-a-hash',reference_text='Hello',language='en',provider_id='tts',model_profile='base')
    values=dict(cue_id='cue',project_id='p',scene_id='s',shot_id='sh',character_id='c',
        text='Hello',voice_profile_id='voice',language='en',target_start_seconds=2,target_end_seconds=1)
    with pytest.raises(ValidationError): DialogueCue(**values)
    cue=DialogueCue(**{**values,'target_end_seconds':3})
    assert check_duration(cue,.6)==(2,2.6)
    with pytest.raises(DialogueTimingReview): check_duration(cue,1.2)


def test_pcm_qc_silence_and_clipping():
    qc=inspect_wav(wav_bytes())
    assert qc['sample_rate']==24000 and qc['channels']==1 and qc['duration_seconds']==.5
    assert qc['human_listening']=='pending'
    with pytest.raises(ValueError,match='silent'): inspect_wav(wav_bytes(amplitude=0))
    raw=bytearray(wav_bytes()); raw[-2:]=struct.pack('<h',32767)
    with pytest.raises(ValueError,match='clipped'): inspect_wav(bytes(raw))


def test_real_provider_binding_has_no_mock_fallback():
    config=MediaProviderSettings(audio_provider='real')
    registry=ProviderFactory.defaults(settings=config).build_registry(config)
    ids={p.provider_id for p in registry.all()}
    assert {'qwen3_tts','ace_step'}<=ids and 'mock-audio' not in ids


@pytest.mark.asyncio
async def test_tts_binary_design_and_refuses_json_audio():
    calls=[]
    def handle(req):
        calls.append(req)
        return httpx.Response(200,content=wav_bytes(),headers={'Content-Type':'audio/wav'})
    provider=Qwen3TTSAudioProvider(settings=MediaProviderSettings(),transport=httpx.MockTransport(handle))
    output=await provider.generate(request())
    assert output.result.provider_metadata['mock'] is False
    assert calls[0].url.path=='/v1/voices/design'
    assert json.loads(calls[0].content)['input']=='林恩，不要靠近地面。'
    assert output.payloads[0].content[:4]==b'RIFF'
    provider.transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'audio':'base64'}))
    with pytest.raises(ValueError,match='binary WAV'): await provider.generate(request())


@pytest.mark.asyncio
async def test_clone_exact_anchor_binary_lineage_and_corruption(tmp_path):
    engine,_=fixture(tmp_path,clips=1)
    anchor=source(engine,'anchor',audio=True)
    ref=MediaReference(reference_type=ReferenceType.VOICE,artifact_id=anchor.artifact_id,
        version=anchor.version,sha256=anchor.metadata['sha256'])
    calls=[]
    def handle(req):
        calls.append(req)
        from urllib.parse import parse_qs
        value=json.loads(parse_qs(req.content.decode())['request'][0])
        assert value['reference_sha256']==ref.sha256
        assert b'RIFF' not in req.content and b'filename=' not in req.content
        assert b'base64' not in req.content
        return httpx.Response(200,content=wav_bytes(),headers={'Content-Type':'audio/wav'})
    from movie_agent.media.transport import MediaReferenceBinaryResolver
    provider=Qwen3TTSAudioProvider(settings=MediaProviderSettings(),
        resolver=MediaReferenceBinaryResolver(engine.artifact_store,engine.binary_store),transport=httpx.MockTransport(handle))
    value=request(SpeechGenerationRequest,project_id=engine.current_project.project_id,
        text='另一句台词。',reference_voice=ref,reference_text='标准声音',voice_profile='voice',voice_profile_version=1)
    output=await provider.generate(value)
    assert output.result.provider_metadata['reference']['sha256']==ref.sha256
    assert output.result.provider_metadata['native_prosody_control'] is False
    value.reference_voice.sha256='0'*64
    with pytest.raises(ValueError,match='hash'): await provider.generate(value)
    assert len(calls)==1


@pytest.mark.asyncio
async def test_music_official_task_recovery_and_same_origin_binary(tmp_path):
    posted=[]
    def handle(req):
        posted.append(req.url.path)
        if req.url.path=='/release_task':
            body=json.loads(req.content)
            assert body['lyrics']=='[Instrumental]' and body['thinking'] is False
            return httpx.Response(200,json={'data':{'task_id':'music-task'}})
        if req.url.path=='/query_result':
            return httpx.Response(200,json={'data':[{'task_id':'music-task','status':1,
                'result':json.dumps([{'file':'/v1/audio?path=output.wav'}])}]})
        return httpx.Response(200,content=wav_bytes(channels=2),headers={'Content-Type':'audio/wav'})
    config=MediaProviderSettings(audio_task_state_root=str(tmp_path/'tasks'))
    for _ in range(2):
        provider=AceStepMusicProvider(settings=config,transport=httpx.MockTransport(handle))
        result=await provider.generate(request(MusicGenerationRequest))
        assert result.result.channels==2 and result.result.provider_metadata['mock'] is False
    assert posted.count('/release_task')==1
    journal=next((tmp_path/'tasks').glob('*.json'))
    journal.write_text(json.dumps({'status':'submitting'}))
    with pytest.raises(ValueError,match='outcome unknown'):
        await provider.generate(request(MusicGenerationRequest))


def test_three_track_mix_ducking_stems_and_subtitle(tmp_path):
    engine,initial=fixture(tmp_path)
    speech=source(engine,'speech',audio=True,duration=.4,frequency=1000)
    music=source(engine,'music',audio=True,duration=2.2,frequency=220)
    timeline=initial.timeline.model_copy(deep=True)
    timeline.audio_tracks.extend([
        AudioTrack(name='Dialogue',cues=[AudioCue(cue_id='dialogue-cue',artifact_id=speech.artifact_id,
            start_time_seconds=.3,duration_seconds=.4,cue_type=AudioPurpose.SPEECH,dialogue_cue_id='canonical-cue')]),
        AudioTrack(name='Music',cues=[AudioCue(cue_id='music-cue',artifact_id=music.artifact_id,
            start_time_seconds=0,duration_seconds=2,cue_type=AudioPurpose.MUSIC,gain_db=-12)])])
    timeline=pin_timeline(timeline,engine.artifact_store,engine.binary_store)
    output=render(engine,request_for(engine,timeline))
    assert output.metadata['qc']['av_end_drift_seconds']==0
    assert output.metadata['render_plan']['policy_version']=='p4d.1'
    assert output.metadata['render_plan']['audio_policy']['native_audio_duck_db']==-16
    assert len(output.metadata['audio_stems'])==3
    assert output.metadata['subtitle_artifact']
    assert render(engine,request_for(engine,timeline)).metadata['qc']['passed']
    # Real FFmpeg stem RMS drops during dialogue, not just a filter-string assertion.
    import subprocess
    stem=next(s for s in output.metadata['audio_stems'] if s['name']=='native_sound_stem')
    artifact=engine.artifact_store.get(stem['artifact_id'],stem['version'])
    path=engine.binary_store.describe(artifact.uri).path
    def rms(start):
        raw=subprocess.run(['ffmpeg','-v','error','-ss',str(start),'-i',str(path),'-t','0.1','-f','f32le','-'],capture_output=True,check=True).stdout
        samples=[v[0] for v in struct.iter_unpack('<f',raw)]
        return math.sqrt(sum(v*v for v in samples)/len(samples))
    assert rms(.45)/rms(.05)<.25
    engine.media_runtime.providers.get('ffmpeg-post').settings.post_subtitle_mode='burn-in'
    burned=render(engine,request_for(engine,timeline))
    assert burned.metadata['render_plan']['audio_policy']['subtitle_mode']=='burn-in'
    assert burned.metadata['qc']['av_end_drift_seconds']==0
    assert burned.metadata['sha256']!=output.metadata['sha256']


def test_canonical_extraction_order_speaker_and_no_overlap(tmp_path):
    from movie_agent.services import MockMovieProduction
    engine=MockMovieProduction(tmp_path)
    brief=ProjectBrief(title='Audio',logline='Audio',story_description='test',target_duration=30)
    result=asyncio.run(engine.run(brief,stop_after_node='shot_gate'))
    project=result.project
    char=project.characters[0]
    shot=project.shots[0]
    shot.duration_seconds=15
    shot.narrative.dialogue=[char.name+': （冷静）林恩，不要靠近地面。',char.name+': 留在舱内，等我回来。']
    for s in project.shots[1:]: s.narrative.dialogue=[];s.performances=[]
    project.screenplay=None
    cues=extract_dialogue(project,{},MediaProviderSettings())
    assert [c.text for c in cues]==['林恩，不要靠近地面。','留在舱内，等我回来。']
    assert cues[0].prosody==['冷静']
    assert cues[0].target_end_seconds+.25<=cues[1].target_start_seconds+.00001
    assert shot.narrative.dialogue[0].startswith(char.name+':')
    original=shot.narrative.dialogue
    shot.narrative.dialogue=['Unknown: Hello']
    with pytest.raises(DialogueTimingReview,match='speaker'):
        extract_dialogue(project,{},MediaProviderSettings())
    shot.narrative.dialogue=original
    shot.duration_seconds=1
    with pytest.raises(DialogueTimingReview): extract_dialogue(project,{},MediaProviderSettings())


def audio_engine(tmp_path, *, studio=False):
    from movie_agent.services import MockMovieProduction
    from movie_agent.domain import WorkflowNodeStatus
    calls=[]
    def transport(req):
        path=req.url.path
        if path=='/health': return httpx.Response(200,json={'ready':True,'data':{'status':'ok','models_initialized':True}})
        calls.append(path)
        if path=='/release_task': return httpx.Response(200,json={'data':{'task_id':'task'}})
        if path=='/query_result': return httpx.Response(200,json={'data':[{'task_id':'task','status':1,'result':json.dumps([{'file':'/v1/audio?path=music.wav'}])}]})
        return httpx.Response(200,content=wav_bytes(channels=2 if path=='/v1/audio' else 1),headers={'Content-Type':'audio/wav'})
    config=MediaProviderSettings(audio_provider='real',post_provider='ffmpeg',audio_task_state_root=str(tmp_path/'tasks'))
    from movie_agent.model_services.wiring import build_spark_runtime
    from movie_agent.model_services.resources import ResourceRuntimeSettings
    from movie_agent.config import LLMConfig
    coordinator=build_spark_runtime(LLMConfig(base_url='http://127.0.0.1:8000/v1',model='fixture'),config,
        settings=ResourceRuntimeSettings(enabled=False,state_path=str(tmp_path/'runtime.json')))
    if studio:
        from movie_agent.services.reasoning_production import ReasoningMovieProduction
        from tests.p2a_fakes import FakeReasoningProvider
        engine=ReasoningMovieProduction(tmp_path,FakeReasoningProvider(),real_roles=[],
            media_settings=config,runtime_coordinator=coordinator)
    else:
        engine=MockMovieProduction(tmp_path,media_settings=config,runtime_coordinator=coordinator)
    for name in ('qwen3_tts','ace_step'):
        engine.media_runtime.providers.get(name).transport=httpx.MockTransport(transport)
    brief=ProjectBrief(title='Audio DAG offline fixture',logline='fixture',story_description='fixture',
        target_duration=20,resolution='160x90',fps=24)
    asyncio.run(engine.run(brief,stop_after_node='shot_gate'))
    project=engine.current_project
    project.screenplay=None
    char=project.characters[0]
    for i,shot in enumerate(project.shots):
        shot.duration_seconds=5
        shot.narrative.dialogue=[char.name+': 不要靠近。',char.name+': 留在舱内。'] if i==0 else []
        for performance in shot.performances: performance.dialogue=None
        source(engine,shot.shot_id,duration=5)
        source(engine,shot.shot_id+'_audio',audio=True,duration=5)
    for node in engine.current_production.graph.nodes:
        if not node.node_id.startswith('audio_') and node.node_id not in {'rough_cut','full_film_review','final_gate','final_render'}:
            node.status=WorkflowNodeStatus.SUCCEEDED
    engine._durable_media_checkpoint()
    return engine,calls


def test_audio_dag_resume_voice_versioning_and_no_upstream_inference(tmp_path):
    engine,calls=audio_engine(tmp_path)
    engine.current_project.characters[0].identity_description='悬浮者少年'
    engine.current_project.characters[0].voice_constraints=['年轻','略带忧郁']
    engine._durable_media_checkpoint()
    before={(a.artifact_id,a.version):a.metadata.get('sha256') for a in engine.artifact_store.list_all() if a.artifact_type.value=='video'}
    result=asyncio.run(engine.run(resume=True))
    assert result.completed
    assert calls.count('/v1/voices/design')==1 and calls.count('/v1/audio/speech')==2
    from movie_agent.services import audio_production as audio
    profile=next(iter(audio.profiles(engine).values()))
    assert all(part in profile.design_instruction for part in ('悬浮者少年','年轻','略带忧郁'))
    anchor=engine.artifact_store.get(profile.reference_artifact_id,profile.reference_artifact_version)
    old_calls=list(calls)
    assert asyncio.run(engine.run(resume=True)).completed
    assert calls==old_calls
    config=audio.controls(engine)
    config['voices'][profile.character_id]={'description':'A different quiet voice','revision':2}
    audio.commit(engine,'audio_controls',config,'audio_controls')
    from movie_agent.domain import WorkflowNodeStatus
    for node in engine.current_production.graph.nodes:
        if node.node_id in {'audio_prepare','audio_post','rough_cut','full_film_review','final_gate','final_render'} or node.node_id.startswith(('audio_voice:','audio_speech:')):
            node.status=WorkflowNodeStatus.PENDING
    from movie_agent.domain import utc_now
    for review in engine.human_gates.all():
        if review.node_id=='final_gate': engine.human_gates._reviews[review.review_id]=review.model_copy(update={'superseded_at':utc_now()})
    engine._durable_media_checkpoint()
    assert asyncio.run(engine.run(resume=True)).completed
    revised=next(iter(audio.profiles(engine).values()))
    assert revised.version==2 and revised.design_instruction=='A different quiet voice'
    assert engine.artifact_store.get(anchor.artifact_id,anchor.version).metadata['sha256']==anchor.metadata['sha256']
    assert calls.count('/v1/voices/design')==2 and calls.count('/v1/audio/speech')==4
    assert before=={(a.artifact_id,a.version):a.metadata.get('sha256') for a in engine.artifact_store.list_all() if a.artifact_type.value=='video' and a.artifact_id!='rough_cut'}


def test_tts_music_allowlist_and_unmeasured_admission(tmp_path):
    from movie_agent.model_services.spark import SparkDockerServiceController, CONTAINERS
    from movie_agent.model_services.resources import ResourceRuntimeSettings
    controller=SparkDockerServiceController(ResourceRuntimeSettings(state_path=str(tmp_path/'state')),runner=lambda *_:None)
    assert controller._container('tts')=='movie-agent-tts'
    assert controller._container('music')=='movie-agent-music'
    with pytest.raises(ValueError): controller._container('arbitrary-container')
    from movie_agent.model_services.wiring import build_spark_runtime
    from movie_agent.config import LLMConfig
    runtime=build_spark_runtime(LLMConfig(base_url='http://127.0.0.1:8000/v1',model='fixture'),MediaProviderSettings(audio_provider='real',tts_resident_gib=0,tts_peak_gib=0),
        settings=ResourceRuntimeSettings(enabled=False,state_path=str(tmp_path/'runtime.json')))
    from movie_agent.model_services.resources import ResourceSnapshot,GiB
    admitted,reason=runtime.can_admit('tts',ResourceSnapshot(total_unified_memory_bytes=128*GiB,available_unified_memory_bytes=120*GiB))
    assert not admitted and 'telemetry' in reason
    assert runtime.manager.get('music').health_path=='/v1/model_inventory'
    runtime.close()


@pytest.mark.asyncio
async def test_music_readiness_uses_loaded_inventory_not_openrouter_catalog():
    from unittest.mock import AsyncMock
    from movie_agent.model_services.spark import SparkDockerModelService
    from tests.test_resource_runtime import Service,Memory
    service=SparkDockerModelService(Service('music',Memory()).descriptor,None,
        health_path='/v1/model_inventory',idle_path='/v1/stats',kind='music')
    for payload,expected in [({'data':[{'id':'music'}]},False),
        ({'data':{'models':[{'name':'music','is_loaded':False}]}},False),
        ({'data':{'models':[{'name':'music','is_loaded':True}]}},True)]:
        service._get=AsyncMock(return_value=httpx.Response(200,json=payload))
        assert await service.health() is expected


def test_audio_commands_scope_and_post_reorder_keep_canonical_tracks(tmp_path):
    from types import SimpleNamespace
    from movie_agent.application.audio_commands import audio_command, AudioProductionCommand
    from movie_agent.application.post_commands import reexport, PostExportCommand
    from movie_agent.application.repository import ProductionStatus
    from movie_agent.application.service import CommandConflict
    from movie_agent.domain import WorkflowNodeStatus
    from movie_agent.services import audio_production as audio
    engine,calls=audio_engine(tmp_path)
    assert asyncio.run(engine.run(resume=True)).completed
    record=SimpleNamespace(status=ProductionStatus.COMPLETED,failure_code=None)
    service=SimpleNamespace(engine=lambda _:engine,
        repository=SimpleNamespace(get=lambda _:record,save=lambda _:None),start=lambda *a,**k:None)
    pid=engine.current_project.project_id
    before=list(calls)
    order=[c.shot_id for t in engine.timeline.video_tracks for c in t.clips][::-1]
    reexport(service,pid,PostExportCommand(shot_order=order,subtitles=False))
    assert [t.name for t in engine.timeline.audio_tracks]==['Production Sound','Dialogue','Music']
    assert engine.current_production.node('audio_post').status==WorkflowNodeStatus.SUCCEEDED
    audio.assemble(engine,engine.current_project)
    shots={s.shot_id:s for s in engine.current_project.shots}
    for cue in engine.timeline.audio_tracks[2].cues:
        for track in engine.timeline.video_tracks:
            for clip in track.clips:
                if cue.start_time_seconds < clip.start_time_seconds+clip.duration_seconds and clip.start_time_seconds < cue.start_time_seconds+cue.duration_seconds:
                    assert cue.scene_id==shots[clip.shot_id].scene_id
    engine.timeline.subtitle_tracks=[]
    for node in engine.current_production.graph.nodes: node.status=WorkflowNodeStatus.SUCCEEDED
    reexport(service,pid,PostExportCommand(subtitles=True))
    assert [c.text for t in engine.timeline.subtitle_tracks for c in t.cues]==[c.text for c in audio.cues(engine)]
    for node in engine.current_production.graph.nodes: node.status=WorkflowNodeStatus.SUCCEEDED
    cue=audio.cues(engine)[0]
    audio_command(service,pid,AudioProductionCommand(action='regenerate_speech',cue_id=cue.cue_id,pace='slow'))
    pending={n.node_id for n in engine.current_production.graph.nodes if n.status==WorkflowNodeStatus.PENDING}
    assert pending=={'audio_prepare','audio_speech:'+cue.cue_id,'audio_post','rough_cut','full_film_review','final_gate','final_render'}
    assert calls==before
    with pytest.raises(CommandConflict,match='upstream'):
        engine.current_production.graph.nodes[0].status=WorkflowNodeStatus.PENDING
        audio_command(service,pid,AudioProductionCommand(action='produce'))


@pytest.mark.asyncio
async def test_music_rejects_foreign_download_and_missing_ready_models(tmp_path):
    def handle(req):
        if req.url.path=='/health': return httpx.Response(200,json={'data':{'status':'ok','models_initialized':False}})
        if req.url.path=='/release_task': return httpx.Response(200,json={'data':{'task_id':'task'}})
        if req.url.path=='/query_result': return httpx.Response(200,json={'data':[{'task_id':'task','status':1,'result':[{'file':'https://foreign.invalid/audio.wav'}]}]})
        raise AssertionError('A foreign audio host must never be contacted')
    provider=AceStepMusicProvider(settings=MediaProviderSettings(audio_task_state_root=str(tmp_path)),transport=httpx.MockTransport(handle))
    assert not await provider.health()
    with pytest.raises(ValueError,match='same service'):
        await provider.generate(request(MusicGenerationRequest))


def test_tts_service_reuses_server_anchor_without_media_upload(tmp_path, monkeypatch):
    from services.tts import app as server
    from fastapi import HTTPException
    content=wav_bytes()
    digest=sha256(content).hexdigest()
    (tmp_path/'anchors').mkdir()
    (tmp_path/'anchors'/f'{digest}.wav').write_bytes(content)
    (tmp_path/'anchors'/f'{digest}.json').write_text(json.dumps({'text':'Reference'}))
    monkeypatch.setattr(server,'cache',tmp_path)
    monkeypatch.setattr(server,'produce',lambda kind,request,anchor:(kind,anchor))
    value={'input':'A second sentence','reference_sha256':digest,'reference_text':'Reference'}
    assert server.speech(json.dumps(value),None)==('base',content)
    with pytest.raises(HTTPException) as wrong:
        server.speech(json.dumps({**value,'reference_text':'Different transcript'}),None)
    assert wrong.value.status_code==422
    with pytest.raises(HTTPException) as missing:
        server.speech(json.dumps({**value,'reference_sha256':'0'*64}),None)
    assert missing.value.status_code==404
