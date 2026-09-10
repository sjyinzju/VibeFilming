"""Opt-in P4C acceptance. Reuses committed bytes; forbids every upstream inference."""
import argparse
import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from movie_agent.application.service import ProductionService
from movie_agent.storage.projects import LocalProjectRepository
from movie_agent.execution.durable_events import DurableLocalEventBus
from movie_agent.services.reasoning_production import ReasoningMovieProduction
from movie_agent.providers.registry import MediaProviderSettings
from tests.p2a_fakes import FakeReasoningProvider

SOURCE=Path('workspace/flux-agent-acceptance-20260907/project_3effeb45f3844bf89c2c96e83770114b')
ROOT=Path('workspace/p4c-post-acceptance')


def prepare(root=ROOT, real=True):
    root=Path(root).resolve()
    root.mkdir(parents=True,exist_ok=True)
    if real:
        if not (root/SOURCE.name).exists(): shutil.copytree(SOURCE,root/SOURCE.name)
        return SOURCE.name
    existing=list(root.glob('project_*/record.json'))
    if existing: return existing[0].parent.name
    service=service_at(root)
    from tests.test_p4c_post import source
    from movie_agent.domain import ProjectBrief, WorkflowNodeStatus
    from movie_agent.media.contracts import Timeline, TimelineClip, VideoTrack
    record=service.create(ProjectBrief(title='Post export fixture',logline='Two deterministic clips',
        story_description='Offline post fixture',target_duration=2,resolution='160x90',fps=24))
    engine=service.engine(record.project.project_id)
    clips=[]
    for i in range(2):
        video=source(engine,f'shot{i}')
        source(engine,f'shot{i}_audio',audio=True,frequency=440+i*220)
        clips.append(TimelineClip(clip_id=f'clip{i}',shot_id=f'shot{i}',artifact_id=video.artifact_id,
                                 start_time_seconds=i,duration_seconds=1))
    engine.timeline=Timeline(timeline_id='fixture',project_id=record.project.project_id,duration_seconds=2,video_tracks=[VideoTrack(clips=clips)])
    for node in engine.current_production.graph.nodes:
        node.status=WorkflowNodeStatus.SUCCEEDED
    engine._durable_media_checkpoint()
    return record.project.project_id


def service_at(root=ROOT):
    async def forbidden(*args,**kwargs):
        raise AssertionError('P4C forbids Qwen/FLUX/H3/VLM/audio inference')
    provider=FakeReasoningProvider()
    provider.submit=forbidden
    def factory(pid):
        engine=ReasoningMovieProduction(Path(root)/pid,provider,real_roles=[],
            event_bus=DurableLocalEventBus(Path(root)/pid/'events'),
            media_settings=MediaProviderSettings(post_provider='ffmpeg',post_temp_root=str(Path(root)/'temp')))
        for component in engine.media_runtime.providers.all():
            for method in ('generate','inspect'):
                if hasattr(component,method): setattr(component,method,forbidden)
        return engine
    return ProductionService(LocalProjectRepository(root),factory)


def accept_http():
    """Actual TCP FastAPI download plus process restarts at gate and completion."""
    import httpx
    ROOT.mkdir(parents=True,exist_ok=True)
    before={p.relative_to(SOURCE).as_posix():sha256(p.read_bytes()).hexdigest() for p in SOURCE.rglob('*') if p.is_file()}
    env={**os.environ,'MOVIE_AGENT_RUN_POST_INTEGRATION':'1'}
    processes=[]
    log=(ROOT/'server.log').open('a',encoding='utf-8')
    def start():
        p=subprocess.Popen([sys.executable,'-m','uvicorn','tests.p4c_server:app','--host','127.0.0.1','--port','8086'],
            env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        processes.append(p)
        for _ in range(100):
            try:
                if httpx.get('http://127.0.0.1:8086/health').status_code==200:return p
            except httpx.HTTPError: pass
            if p.poll() is not None:raise RuntimeError('Acceptance server exited; see server.log')
            time.sleep(.2)
        raise RuntimeError('Acceptance server startup timeout')
    def stop(p):
        p.terminate();p.wait(timeout=20)
    report={}
    try:
        process=start()
        with httpx.Client(base_url='http://127.0.0.1:8086',timeout=120) as client:
            result=client.post('/p4c/start');result.raise_for_status()
            pid=result.json()['project_id']
            def wait_for(states):
                deadline=time.monotonic()+1800
                while time.monotonic()<deadline:
                    state=client.get(f'/projects/{pid}/studio').json()
                    if state['status']=='failed':raise RuntimeError('Post acceptance failed: '+str(state['failure_code']))
                    if state['status'] in states:return state
                    time.sleep(1)
                raise TimeoutError('Post acceptance timeout')
            state=wait_for({'waiting_human','completed'})
            rough=next(a for a in state['artifacts'] if a['artifact_id']=='rough_cut' and a['selected'])
            stop(process);process=start()
            restored=client.get(f'/projects/{pid}/studio').json()
            assert restored['status']==state['status']
            assert next(a for a in restored['artifacts'] if a['artifact_id']=='rough_cut' and a['selected'])==rough
            if state['status']=='waiting_human':
                review=next(r for r in state['reviews'] if r['node_id']=='final_gate' and not r['superseded_at'])
                assert review['target_sha256']==rough['metadata']['sha256']
                client.post(f"/reviews/{review['review_id']}/resolve",json={'approved':True,
                    'notes':'P4C technical acceptance fixture; validates approval execution path, not a human aesthetic verdict.'}).raise_for_status()
                client.post(f'/projects/{pid}/resume').raise_for_status()
                state=wait_for({'completed'})
            final=next(a for a in state['artifacts'] if a['artifact_id']=='final_film' and a['selected'])
            for artifact,name in [(rough,'rough_cut.mp4'),(final,'final_film.mp4')]:
                digest=sha256();size=0
                path=f"/projects/{pid}/artifacts/{artifact['artifact_id']}/versions/{artifact['version']}/download"
                with client.stream('GET',path) as response:
                    response.raise_for_status()
                    assert response.status_code==200 and response.headers['content-disposition'].startswith('attachment;')
                    assert response.headers['content-type']=='video/mp4'
                    with (ROOT/name).open('wb') as output:
                        for chunk in response.iter_bytes():digest.update(chunk);size+=len(chunk);output.write(chunk)
                    assert size==int(response.headers['content-length']) and digest.hexdigest()==artifact['metadata']['sha256']
                    report[name]={'endpoint':path,'status':200,'headers':dict(response.headers),'sha256':digest.hexdigest(),'size_bytes':size}
            convenience=client.get(f'/projects/{pid}/exports/final')
            assert convenience.status_code==200 and sha256(convenience.content).hexdigest()==final['metadata']['sha256']
            stop(process);process=start()
            after=client.get(f'/projects/{pid}/studio').json()
            assert after['status']=='completed'
            assert len(after['jobs'])==len(state['jobs'])
            report.update(project_id=pid,rough=rough,final=final,upstream_inference_calls=0,
                checkpoint_resume={'rough_reused':True,'pending_gate_retained':True,'final_reused':True})
            manifest=client.get(f"/projects/{pid}/artifacts/{final['metadata']['manifest_artifact_id']}/versions/1/download")
            manifest.raise_for_status();(ROOT/'render_manifest.json').write_bytes(manifest.content)
        unchanged={p.relative_to(SOURCE).as_posix():sha256(p.read_bytes()).hexdigest() for p in SOURCE.rglob('*') if p.is_file()}
        assert unchanged==before
        report['original_workspace_unchanged']=True
        (ROOT/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'final_uri':final['uri'],'sha256':final['metadata']['sha256'],'qc':final['metadata']['qc']},ensure_ascii=False))
    finally:
        for p in processes:
            if p.poll() is None:stop(p)
        log.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--accept',action='store_true');args=parser.parse_args()
    if not args.accept:parser.error('Pass --accept to run existing-media acceptance')
    accept_http()
