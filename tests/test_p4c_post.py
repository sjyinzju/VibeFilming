"""Real decoder/encoder tests with tiny deterministic, non-AI source media."""
import asyncio
from hashlib import sha256
from pathlib import Path
import shutil
import subprocess

import pytest
from movie_agent.domain import Artifact, ArtifactType, Project, ProjectBrief, Provenance, GenerationJob
from movie_agent.media.contracts import (Timeline, VideoTrack, TimelineClip, AudioTrack, AudioCue,
    AudioPurpose, PostProductionRequest, SubtitleTrack, SubtitleCue)
from movie_agent.media.post import pin_timeline
from movie_agent.providers.registry import MediaProviderSettings
from movie_agent.services import MockMovieProduction
from movie_agent.execution import JobManager

pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg'),reason='FFmpeg required')


def source(engine,identity,*,audio=False,duration=1.125,fps=25,size='96x64',frequency=440,version=None,start_time=0):
    path=engine.workspace/f'{identity}-fixture.{'wav' if audio else 'mp4'}'
    argv=['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
          f'sine=frequency={frequency}:sample_rate=44100:duration={duration}' if audio
          else f'testsrc2=size={size}:rate={fps}:duration={duration}']
    subprocess.run(argv+([] if audio else ['-c:v','libx264','-pix_fmt','yuv420p',
        '-vf',f'setpts=PTS+{start_time}/TB','-fps_mode','passthrough'])+[str(path)],check=True,capture_output=True)
    data=path.read_bytes()
    version=version or len(engine.artifact_store.list_versions(identity))+1
    record=engine.binary_store.put(identity,version,data,mime_type='audio/wav' if audio else 'video/mp4',extension='wav' if audio else 'mp4')
    artifact=engine.artifact_store.register(Artifact(artifact_id=identity,version=version,uri=record.uri,
        artifact_type=ArtifactType.AUDIO if audio else ArtifactType.VIDEO,source_job_id='source:'+identity.removesuffix('_audio'),
        metadata={'mock':False,'sha256':sha256(data).hexdigest(),'media':{'modality':'audio' if audio else 'video',
        'purpose':'generated_native_audio' if audio else 'shot_video','encoding':{'mime_type':record.mime_type,
        'format':record.extension},'mock':False}},provenance=Provenance(provider_id='ffmpeg-fixture',
        project_id=engine.current_project.project_id,shot_id=identity.removesuffix('_audio'))))
    engine.artifact_store.select(identity,version)
    path.unlink()
    return artifact


def fixture(tmp_path,*,clips=2):
    engine=MockMovieProduction(tmp_path,media_settings=MediaProviderSettings(post_provider='ffmpeg',post_temp_root=str(tmp_path/'temp')))
    engine.current_project=Project(brief=ProjectBrief(title='Post fixture',logline='test',story_description='test',target_duration=2))
    manager=JobManager(engine.event_bus,engine.trace_id)
    engine.job_manager=manager;engine.media_runtime.bind_jobs(manager);engine.media_runtime.checkpoint_callback=None
    videos=[];cues=[]
    for i in range(clips):
        video=source(engine,f'shot{i}',duration=.8 if i else 1.125)
        videos.append(TimelineClip(artifact_id=video.artifact_id,start_time_seconds=i,duration_seconds=1,shot_id=f'shot{i}'))
        if i==0:
            audio=source(engine,f'shot{i}_audio',audio=True,duration=.92)
            cues.append(AudioCue(artifact_id=audio.artifact_id,start_time_seconds=i,duration_seconds=1,cue_type=AudioPurpose.GENERATED_NATIVE_AUDIO,shot_id=f'shot{i}'))
    timeline=pin_timeline(Timeline(project_id=engine.current_project.project_id,duration_seconds=clips,
        video_tracks=[VideoTrack(clips=videos)],audio_tracks=[AudioTrack(cues=cues)],
        subtitle_tracks=[SubtitleTrack(language='zh',cues=[SubtitleCue(start_time_seconds=0,end_time_seconds=1,text='测试字幕')])]),engine.artifact_store,engine.binary_store)
    return engine, request_for(engine,timeline)


def request_for(engine,timeline,**kwargs):
    artifact=engine.artifact_store.create_structured('timeline',timeline.model_dump(mode='json'),artifact_type=ArtifactType.TIMELINE,
        provenance=Provenance(project_id=timeline.project_id))
    return PostProductionRequest(job_id='test-post',project_id=timeline.project_id,timeline=timeline,
        timeline_artifact_id=artifact.artifact_id,timeline_version=artifact.version,timeline_sha256=artifact.metadata['sha256'],
        output_artifact_id='rough_cut',width=160,height=90,fps=24,**kwargs)


def render(engine,request):
    job=GenerationJob(job_id='test-post',project_id=request.project_id,node_id='rough_cut',task='post',idempotency_key='test-post',retry_budget=0)
    return asyncio.run(engine.media_runtime.post_process(job,request))


def test_render_conform_qc_subtitles_manifest_resume_and_versions(tmp_path):
    engine,request=fixture(tmp_path)
    artifact=render(engine,request)
    assert artifact.metadata['mock'] is False
    plan=artifact.metadata['render_plan']
    assert plan['segments'][0]['video_facts']['fps']==25
    assert plan['segments'][0]['video_facts']['duration']>1
    assert plan['segments'][1]['video_facts']['duration']<1
    assert plan['segments'][1]['generated_silence']
    assert 'generated_silence' in plan['segments'][1]['conform_actions']
    assert 'audio_trim' in plan['segments'][0]['conform_actions']
    assert artifact.metadata['qc']['passed']
    assert artifact.metadata['qc']['av_end_drift_seconds']<=1/24
    assert any(event.payload.get('remote_event')=='ffmpeg_progress' for event in engine.event_bus.events())
    assert artifact.metadata['loudness']['output']
    assert engine.artifact_store.get(artifact.metadata['manifest_artifact_id'])
    assert engine.artifact_store.get(artifact.metadata['subtitle_artifact']['artifact_id'])
    assert render(engine,request).uri==artifact.uri
    from movie_agent.domain import JobStatus
    committed_job=engine.job_manager.get(artifact.source_job_id)
    engine.job_manager._jobs[committed_job.job_id]=committed_job.model_copy(update={'status':JobStatus.RUNNING})
    assert render(engine,request).uri==artifact.uri
    assert engine.job_manager.get(committed_job.job_id).status==JobStatus.SUCCEEDED
    oldhash=artifact.metadata['sha256']
    source(engine,'shot0',duration=1.5,version=2)
    # New selected v2 cannot change the pinned v1 render.
    assert render(engine,request).metadata['sha256']==oldhash
    changed=request.model_copy(update={'width':192,'height':108})
    second=render(engine,changed)
    assert second.version==2 and second.metadata['render_plan_id']!=artifact.metadata['render_plan_id']
    assert engine.artifact_store.get('rough_cut',1).metadata['sha256']==oldhash
    assert list((tmp_path/'temp').iterdir())==[]
    changed_timeline=request.timeline.model_copy(deep=True)
    changed_timeline.video_tracks[0].clips[0].version=2
    changed_timeline.video_tracks[0].clips[0].sha256=engine.artifact_store.get('shot0',2).metadata['sha256']
    third=render(engine,request_for(engine,changed_timeline))
    assert third.version==3
    assert third.metadata['render_plan']['segments'][0]['video']['version']==2


def test_one_clip_and_mock_rejection_and_hash_pin(tmp_path):
    engine,request=fixture(tmp_path,clips=1)
    assert render(engine,request).metadata['qc']['video']['frame_count']==24
    provider=engine.media_runtime.providers.get('ffmpeg-post')
    bad=request.model_copy(deep=True)
    bad.timeline.video_tracks[0].clips[0].sha256='0'*64
    bad=request_for(engine,bad.timeline)
    with pytest.raises(ValueError,match='mismatch'):
        asyncio.run(provider.prepare_request(bad))
    original=engine.artifact_store.get('shot0_audio',1)
    engine.artifact_store._artifacts=[a.model_copy(update={'metadata':{**a.metadata,'mock':True}})
        if a.artifact_id==original.artifact_id else a for a in engine.artifact_store._artifacts]
    with pytest.raises(ValueError,match='Mock'):
        asyncio.run(provider.prepare_request(request))


def test_corrupted_input_and_timeout_cleanup(tmp_path):
    engine,request=fixture(tmp_path,clips=1)
    provider=engine.media_runtime.providers.get('ffmpeg-post')
    provider.settings.post_timeout=.000001
    with pytest.raises(TimeoutError): asyncio.run(provider.prepare_request(request))
    provider.settings.post_timeout=30
    path=engine.binary_store.describe(engine.artifact_store.get('shot0',1).uri).path
    path.write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='hash'): asyncio.run(provider.prepare_request(request))


def test_silence_trim_source_ranges_invalid_transition_and_missing_pins(tmp_path):
    engine,request=fixture(tmp_path,clips=1)
    timeline=request.timeline.model_copy(deep=True)
    timeline.audio_tracks=[]
    timeline.video_tracks[0].clips[0].source_in_seconds=.25
    timeline.video_tracks[0].clips[0].trim_end_seconds=.1
    output=render(engine,request_for(engine,timeline))
    assert output.metadata['loudness']['all_silence'] is True
    assert output.metadata['render_plan']['segments'][0]['video_source_in']==.25
    provider=engine.media_runtime.providers.get('ffmpeg-post')
    for field,value in [('transition','crossfade'),('version',None)]:
        invalid=timeline.model_copy(deep=True)
        setattr(invalid.video_tracks[0].clips[0],field,value)
        with pytest.raises(ValueError):asyncio.run(provider.prepare_request(request_for(engine,invalid)))


def test_ffmpeg_failure_timeout_cleanup_and_partial_retry(tmp_path,monkeypatch):
    engine,request=fixture(tmp_path,clips=1)
    provider=engine.media_runtime.providers.get('ffmpeg-post')
    prepared=asyncio.run(provider.prepare_request(request))
    original=provider._run
    def fail(*args,**kwargs):raise TimeoutError('test worker timeout')
    monkeypatch.setattr(provider,'_run',fail)
    with pytest.raises(Exception):render(engine,prepared)
    assert not engine.artifact_store.list_versions('rough_cut')
    assert [p.name for p in (tmp_path/'temp').iterdir()]==['diagnostics']
    monkeypatch.setattr(provider,'_run',original)
    # Unregistered file orphan from crash is never downloadable or a succeeded artifact.
    orphan=engine.binary_store.root/'rough_cut'/'v1.mp4';orphan.parent.mkdir(parents=True,exist_ok=True)
    orphan.write_bytes(b'partial')
    result=render(engine,prepared)
    assert result.version==1 and result.metadata['qc']['passed']
    import threading
    with pytest.raises(ValueError,match='failed'):
        provider._run(['ffmpeg','-invalid-flag'],tmp_path,threading.Event(),lambda *a:None,'test')
    provider.settings.post_timeout=.001
    import sys
    with pytest.raises(TimeoutError):
        provider._run([sys.executable,'-c','import time; time.sleep(10)'],tmp_path,threading.Event(),lambda *a:None,'timeout')


def test_wrong_native_lineage_and_audio_mock_even_when_unplaced(tmp_path):
    engine,request=fixture(tmp_path,clips=1)
    provider=engine.media_runtime.providers.get('ffmpeg-post')
    audio=source(engine,'different_audio',audio=True)
    timeline=request.timeline.model_copy(deep=True)
    cue=timeline.audio_tracks[0].cues[0]
    cue.artifact_id=audio.artifact_id;cue.version=audio.version;cue.sha256=audio.metadata['sha256']
    with pytest.raises(ValueError,match='source job'):
        asyncio.run(provider.prepare_request(request_for(engine,timeline)))


def test_exact_download_project_guard_range_and_streaming(tmp_path):
    import httpx
    from movie_agent.api.app import create_app
    from movie_agent.application.service import ProductionService
    from movie_agent.storage.projects import LocalProjectRepository
    from movie_agent.application.repository import ProjectRecord
    from movie_agent.media.download import DownloadRecord,download_response
    engine,request=fixture(tmp_path/'media',clips=1)
    artifact=render(engine,request)
    repo=LocalProjectRepository(tmp_path/'repo');repo.save(ProjectRecord(project=engine.current_project))
    service=ProductionService(repo,lambda pid:engine);service.engines[request.project_id]=engine
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service)),base_url='http://test') as client:
            url=f'/projects/{request.project_id}/artifacts/rough_cut/versions/1/download'
            response=await client.get(url)
            assert response.status_code==200 and response.headers['content-type']=='video/mp4'
            assert response.headers['content-disposition']=='attachment; filename="rough_cut_v1.mp4"'
            assert int(response.headers['content-length'])==len(response.content)
            assert sha256(response.content).hexdigest()==artifact.metadata['sha256']
            assert response.headers['etag']=='"'+artifact.metadata['sha256']+'"'
            for invalid in [url.replace('versions/1','versions/0'),url.replace('versions/1','versions/99'),
                url.replace('rough_cut','unknown'),url.replace(request.project_id,'project_'+'0'*32)]:
                assert (await client.get(invalid)).status_code in (404,422)
            preview=await client.get(f'/artifacts/rough_cut/preview?project_id={request.project_id}&version=1',headers={'Range':'bytes=100-199'})
            assert preview.status_code==206 and len(preview.content)==100 and 'content-disposition' not in preview.headers
            for identity in [artifact.metadata['manifest_artifact_id'],artifact.metadata['subtitle_artifact']['artifact_id']]:
                sidecar=await client.get(url.replace('rough_cut',identity))
                assert sidecar.status_code==200
        path=tmp_path/'large.mp4'
        with path.open('wb') as stream:
            for _ in range(5):stream.write(b'x'*1048576)
        record=DownloadRecord(path,'video/mp4','mp4',path.stat().st_size,'a'*64)
        streamed=download_response(artifact,record)
        sizes=[len(chunk) async for chunk in streamed.body_iterator]
        assert sizes==[1048576]*5
    asyncio.run(scenario())


def test_gate_restart_post_only_reexport_and_approval_pin(tmp_path):
    from scripts.accept_post import prepare,service_at
    from movie_agent.application.post_commands import reexport,PostExportCommand
    from movie_agent.application.studio import studio_snapshot
    pid=prepare(tmp_path,real=False)
    async def scenario():
        service=service_at(tmp_path)
        reexport(service,pid,PostExportCommand())
        await service.tasks[pid]
        assert service.repository.get(pid).status.value=='waiting_human'
        rough=service.engine(pid).artifact_store.get('rough_cut')
        service=service_at(tmp_path)
        engine=service.engine(pid)
        review=engine.human_gates.pending_for_node('final_gate')
        assert review.target_sha256==rough.metadata['sha256']
        subject=next(s for s in studio_snapshot(service,pid).review_subjects if s.review_id==review.review_id)
        assert subject.sources[0].artifacts[0].version==rough.version
        service.resolve_review(review.review_id,True,'Test approval')
        # Newly selected video after approval cannot enter this final render.
        source(engine,'shot0',duration=1.6,version=2)
        service.start(pid,resume=True);await service.tasks[pid]
        assert service.repository.get(pid).status.value=='completed'
        first=engine.artifact_store.get('final_film')
        assert first.metadata['render_plan_id']==rough.metadata['render_plan_id']
        assert first.metadata['render_plan']['segments'][0]['video']['version']==1
        service=service_at(tmp_path);engine=service.engine(pid)
        assert engine.artifact_store.get('final_film').uri==first.uri
        old_jobs={j.job_id for j in engine._all_jobs()}
        reexport(service,pid,PostExportCommand(resolution='192x108',use_selected_sources=True))
        await service.tasks[pid]
        assert service.repository.get(pid).status.value=='waiting_human'
        new_review=engine.human_gates.pending_for_node('final_gate')
        service.resolve_review(new_review.review_id,True,'Re-export test approval')
        service.start(pid,resume=True);await service.tasks[pid]
        assert service.repository.get(pid).status.value=='completed'
        final=engine.artifact_store.get('final_film')
        assert final.version==2 and final.metadata['width']==192
        assert final.metadata['render_plan']['segments'][0]['video']['version']==2
        assert engine.artifact_store.get('final_film',1).metadata['sha256']==first.metadata['sha256']
        assert all(j.task=='post' for j in engine._all_jobs() if j.job_id not in old_jobs)
    asyncio.run(scenario())


def test_fractional_delivery_fps_is_cfr_with_mux_rounding_tolerance(tmp_path):
    engine,request=fixture(tmp_path)
    output=render(engine,request.model_copy(update={'fps':23.976}))
    assert output.metadata['qc']['checks']['CFR']
    assert abs(output.metadata['qc']['video']['fps']-23.976)<.0001
    assert output.metadata['qc']['av_end_drift_seconds']<1/23.976


def test_nonzero_source_pts_is_reset_before_source_trim(tmp_path):
    engine,request=fixture(tmp_path,clips=1)
    video=source(engine,'shot0',version=2,start_time=2)
    timeline=request.timeline.model_copy(deep=True)
    clip=timeline.video_tracks[0].clips[0]
    clip.version=2;clip.sha256=video.metadata['sha256'];clip.source_in_seconds=.1
    output=render(engine,request_for(engine,timeline))
    assert output.metadata['render_plan']['segments'][0]['video_facts']['start_time']==2
    assert output.metadata['qc']['video']['start_time']==0
    assert output.metadata['qc']['video']['frame_count']==24
