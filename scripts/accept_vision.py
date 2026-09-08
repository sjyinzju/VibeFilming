"""Opt-in P4B acceptance: inspect committed Scene 01 bytes; never generate media."""

import argparse
import asyncio
from contextlib import suppress
from hashlib import sha256
import json
from pathlib import Path
import shutil

from movie_agent.domain import WorkflowNodeStatus, utc_now
from movie_agent.application.repository import ProductionStatus

SOURCE = Path('workspace/flux-agent-acceptance-20260907/project_3effeb45f3844bf89c2c96e83770114b')
ROOT = Path('workspace/p4b-vision-acceptance')
TARGET = 'video_SCENE_01-SHOT-01'


def prepare_acceptance(source=SOURCE, root=ROOT):
    """Isolated acceptance copy retains source versions; original project is untouched."""
    source, root=source.resolve(),root.resolve()
    destination=root/source.name
    if not destination.exists():
        shutil.copytree(source,destination)
    return source.name


async def inspect_existing(service, project_id, *, root=ROOT, inspection_revision=1):
    engine=service.engine(project_id)
    coordinator=engine.media_runtime.runtime_coordinator
    assert coordinator and coordinator.settings.enabled, 'P4A must own the real Vision lease'
    provider=engine.media_runtime.providers.get('qwen3_vl')
    # Explicit acceptance scope: forbid every reasoning/media-generation call,
    # including accidental dispatch introduced by future refactors.
    async def forbidden_inference(*args, **kwargs):
        raise AssertionError('P4B acceptance forbids Qwen reasoning, image, video and audio generation')
    guarded=[]
    for component, method in [(engine.llm_provider, 'submit'),
            *[(p, 'generate') for p in engine.media_runtime.providers.all() if p is not provider and hasattr(p, 'generate')]]:
        guarded.append((component,method,getattr(component,method)))
        setattr(component,method,forbidden_inference)
    target=engine.artifact_store.get(TARGET,1)
    assert target and target.provenance.provider_id=='comfyui-video'
    assert target.metadata['duration_seconds']==15 and not target.metadata['mock']
    shot=next(s for s in engine.current_project.shots if s.shot_id=='SCENE_01-SHOT-01')
    canonical=engine.current_project.model_dump_json()
    old_jobs={j.job_id for j in engine._all_jobs() if j.task!='vision'}
    report_file=root/'acceptance.json'
    report_file.parent.mkdir(parents=True,exist_ok=True)
    # A successful committed review is reused, even if a prior browser disconnected.
    from movie_agent.execution import JobManager
    manager=JobManager(engine.event_bus,engine.trace_id)
    engine.job_manager=manager;engine.media_runtime.bind_jobs(manager)
    for old in engine._restored_jobs.values():
        if old.status.value=='succeeded':
            manager._jobs[old.job_id]=old.model_copy(deep=True)
            manager._idempotency[old.idempotency_key]=old.job_id
    samples=[]
    milestones={}
    telemetry_errors=[]
    telemetry_file=root/f'telemetry-r{inspection_revision}.json'
    previous_telemetry=json.loads(telemetry_file.read_text(encoding='utf-8')) if telemetry_file.exists() else None
    inspection_dispatched=False
    vlm=coordinator.manager.get('vlm')
    controller=vlm.controller
    original_control=controller.runner
    async def measured_control(command, timeout):
        from time import perf_counter
        import traceback
        started=perf_counter()
        row={'started_at':utc_now().isoformat(),'command':command,'timeout_seconds':timeout}
        try:
            output=await original_control(command,timeout)
            row['outcome']='completed'
            return output
        except BaseException:
            row['outcome']='failed'
            row['traceback']=traceback.format_exc()
            raise
        finally:
            row['elapsed_seconds']=perf_counter()-started
            with (root/f'control-r{inspection_revision}.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(row)+'\n')
    controller.runner=measured_control
    async def capture(label):
        try:
            sample=await coordinator.snapshot(project_id)
            milestones[label]=sample.model_dump(mode='json')
        except Exception as error:
            telemetry_errors.append({'phase':label,'error_type':type(error).__name__})
    original_start, original_prepare, original_inspect = vlm.start, provider.prepare, provider.inspect
    async def measured_start():
        await capture('before_startup')
        try:
            result=await original_start()
        except BaseException:
            import traceback
            (root/f'startup-failure-r{inspection_revision}.txt').write_text(traceback.format_exc(),encoding='utf-8')
            raise
        await capture('model_ready')
        return result
    async def measured_prepare(request):
        body, metadata=await original_prepare(request)
        (root/f'request-r{inspection_revision}.json').write_text(json.dumps(body,ensure_ascii=False,indent=2),encoding='utf-8')
        await capture('before_inference')
        return body,metadata
    async def measured_inspect(request):
        nonlocal inspection_dispatched
        inspection_dispatched=True
        result=await original_inspect(request)
        # Persist a returned response before optional diagnostics. A telemetry
        # outage must never discard completed model work or turn it into a retry.
        (root/f'provider-result-r{inspection_revision}.json').write_text(result.model_dump_json(indent=2),encoding='utf-8')
        await capture('after_inference')
        return result
    vlm.start, provider.prepare, provider.inspect = measured_start, measured_prepare, measured_inspect
    async def observe():
        while True:
            with suppress(Exception):
                sample=await coordinator.snapshot(project_id)
                samples.append({'phase':coordinator.manager.get('vlm').descriptor.status.value,
                                **sample.model_dump(mode='json')})
                (root/f'telemetry-r{inspection_revision}.json').write_text(json.dumps(
                    {'milestones':milestones,'samples':samples},ensure_ascii=False),encoding='utf-8')
            await asyncio.sleep(2)
    monitor=asyncio.create_task(observe())
    record=service.repository.get(project_id);record.status=ProductionStatus.RUNNING;service.repository.save(record)
    engine.current_production.set_status('visual_semantic_critic',WorkflowNodeStatus.RUNNING,progress=.05)
    try:
        evaluation=await engine._vision_evaluation(engine.current_project,shot,target,
                                                   inspection_revision=inspection_revision)
        await capture('after_lease_release')
        engine._record_evaluation(engine.current_project,evaluation)
        result=next(i for i in engine.media_runtime.inspections if i.result_id==evaluation.inspection_result_id)
        assert result.provider_id=='qwen3_vl' and result.target_artifact_version==1
        assert not result.provider_metadata.get('mock')
        engine.current_production.set_status('visual_semantic_critic',WorkflowNodeStatus.SUCCEEDED)
        engine._durable_media_checkpoint()
        record.status=ProductionStatus.PAUSED
        service.repository.save(record)
        assert engine.current_project.model_dump_json()==canonical, 'inspection mutated canonical state'
        assert {j.job_id for j in engine._all_jobs() if j.task!='vision'}==old_jobs, 'unexpected upstream inference'
        previous_report=root/f'acceptance-r{inspection_revision}.json'
        if not inspection_dispatched and previous_report.exists():
            previous=json.loads(previous_report.read_text(encoding='utf-8'))
            if previous['result']['result_id']==result.result_id:
                return {**previous,'reused_committed_inspection':True}
        if not inspection_dispatched and previous_telemetry:
            # Recover diagnostics for the actual dispatch, not a cached UI replay.
            samples=previous_telemetry['samples']
            milestones=previous_telemetry['milestones']
            telemetry_errors.extend(previous_telemetry.get('errors', []))
            for phase in ('before_startup','model_ready','before_inference','after_inference','after_lease_release'):
                if phase not in milestones:
                    telemetry_errors.append({'phase':phase,'error_type':'missing_prior_dispatch_measurement'})
        evidence_artifact=next(a for a in engine.artifact_store.list_all()
            if a.metadata.get('purpose')=='vision_inspection' and
            a.metadata.get('inspection_fingerprint')==result.inspection_fingerprint)
        observation=next((o for o in reversed(coordinator.observations)
            if o.service_id=='vlm' and o.job_id==evidence_artifact.source_job_id),None)
        evidence_path=engine.artifact_store.data_dir/evidence_artifact.artifact_id/f'v{evidence_artifact.version}.json'
        before=observation.resource_before if observation else None
        minimum=min(samples,key=lambda s:s['available_unified_memory_bytes']) if samples else None
        try:
            oom=await vlm.oom_killed()
        except Exception as error:
            oom=None
            telemetry_errors.append({'phase':'final_oom_check','error_type':type(error).__name__})
        report={'completed_at':utc_now().isoformat(),'provider':'qwen3_vl','model':provider.settings.vision_model,
            'source_artifact':{'artifact_id':target.artifact_id,'version':1,'sha256':result.target_sha256},
            'capabilities':{'video_url':True,'video_transport':provider.settings.vision_video_transport,
                'original_mp4_direct':provider.settings.vision_video_transport=='native',
                'mixed_image_video':len(result.provider_metadata.get('staged_media',[]))>1,
                'native_sampling':result.provider_metadata.get('sampling'),
                'response_format':result.provider_metadata.get('structured_output'),
                'reasoning':result.provider_metadata.get('attempts')},
            'result':result.model_dump(mode='json'),'resource_observation':observation.model_dump(mode='json') if observation else None,
            'inspection_artifact':{'uri':evidence_artifact.uri,'version':evidence_artifact.version,
                'sha256':sha256(evidence_path.read_bytes()).hexdigest(),'source_job_id':evidence_artifact.source_job_id},
            'startup_and_execution_samples':samples,'sampled_minimum':minimum, 'milestones':milestones,
            'telemetry_errors':telemetry_errors,
            'inspection_revision':inspection_revision,
            'cold_baseline':before.model_dump(mode='json') if before else None,
            'oom':oom,'reused_committed_inspection':not inspection_dispatched,'upstream_inference_count':0,
            'source_project_unchanged':True,'acceptance_scope':'one immutable Scene 01 video; no repair generation'}
        report_file.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        (root/f'acceptance-r{inspection_revision}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        return report
    except BaseException:
        import traceback
        (root/f'failure-r{inspection_revision}.txt').write_text(traceback.format_exc(),encoding='utf-8')
        engine.current_production.set_status('visual_semantic_critic',WorkflowNodeStatus.FAILED)
        engine._durable_media_checkpoint()
        record.status=ProductionStatus.FAILED;service.repository.save(record)
        raise
    finally:
        controller.runner=original_control
        vlm.start, provider.prepare, provider.inspect = original_start, original_prepare, original_inspect
        for component, method, original in guarded:
            setattr(component,method,original)
        monitor.cancel()
        with suppress(asyncio.CancelledError):await monitor
        if not inspection_dispatched and previous_telemetry:
            telemetry_file.write_text(json.dumps(previous_telemetry,ensure_ascii=False),encoding='utf-8')
        elif inspection_dispatched:
            telemetry_file.write_text(json.dumps({'milestones':milestones,'samples':samples,
                'errors':telemetry_errors},ensure_ascii=False),encoding='utf-8')


async def main(source,root):
    from movie_agent.config import LLMConfig
    from movie_agent.providers.openai_compatible import OpenAICompatibleLLMProvider
    from movie_agent.providers.registry import MediaProviderSettings
    from movie_agent.model_services.wiring import build_spark_runtime
    from movie_agent.execution.durable_events import DurableLocalEventBus
    from movie_agent.application.service import ProductionService
    from movie_agent.storage.projects import LocalProjectRepository
    from movie_agent.services.reasoning_production import ReasoningMovieProduction
    project_id=prepare_acceptance(source,root)
    llm=OpenAICompatibleLLMProvider(LLMConfig.from_env())
    settings=MediaProviderSettings.from_env().model_copy(update={'vision_provider':'qwen3_vl'})
    coordinator=build_spark_runtime(llm.config,settings)
    service=ProductionService(LocalProjectRepository(root),lambda pid:ReasoningMovieProduction(root/pid,llm,
        event_bus=DurableLocalEventBus(root/pid/'events'),media_settings=settings,runtime_coordinator=coordinator))
    try:
        report=await inspect_existing(service,project_id,root=root)
        print(json.dumps({'decision':report['result']['decision'],'latency_seconds':report['result']['provider_metadata']['latency_seconds'],
                          'report':str(root/'acceptance.json'),'oom':report['oom']},ensure_ascii=False))
    finally:
        await service.shutdown();await llm.aclose();coordinator.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',action='store_true',help='explicitly run real Qwen3-VL, never Qwen/FLUX/H3 generation')
    parser.add_argument('--source',type=Path,default=SOURCE);parser.add_argument('--root',type=Path,default=ROOT)
    args=parser.parse_args()
    if not args.run:parser.error('--run is required for real VLM acceptance')
    asyncio.run(main(args.source.resolve(),args.root.resolve()))
