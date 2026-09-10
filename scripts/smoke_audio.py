"""Explicit isolated calibration before production admission budgets exist.

Uses P4A ownership, Docker allow-list, health, unified-memory telemetry and TTL
eviction. Bootstrap inference is allowed only with every other managed container
stopped and no active lease. It never changes production memory estimates itself.
"""
import argparse
import asyncio
from contextlib import suppress
from datetime import datetime, UTC
import json
from pathlib import Path
from time import perf_counter

import httpx

from movie_agent.config import LLMConfig
from movie_agent.domain import ArtifactType, GenerationJob, Project, ProjectBrief
from movie_agent.execution.durable_events import DurableLocalEventBus
from movie_agent.media.contracts import (AudioPurpose, CharacterVoiceProfile,
    MediaModality, MediaReference, ReferenceType, VoiceDesignRequest,
    SpeechGenerationRequest, MusicGenerationRequest)
from movie_agent.media.compilers import GenericAudioPromptCompiler
from movie_agent.model_services.resources import ResourceRuntimeSettings, GiB
from movie_agent.model_services.wiring import build_spark_runtime
from movie_agent.providers.registry import MediaProviderSettings
from movie_agent.services.mock_production import MockMovieProduction


async def run(service_id, root):
    root.mkdir(parents=True, exist_ok=True)
    report_path = root / f'{service_id}-acceptance.json'
    if report_path.exists() and json.loads(report_path.read_text(encoding='utf-8')).get('status') == 'passed':
        print(f'{service_id}: successful calibration already checkpointed; reuse {report_path}',flush=True)
        return
    settings = MediaProviderSettings.from_env().model_copy(update={
        'audio_provider': 'real', 'post_provider': 'ffmpeg',
        'audio_task_state_root': str(root / 'tasks')})
    resource_settings = ResourceRuntimeSettings.from_env().model_copy(update={
        'enabled': True, 'warm_idle_ttl': 10, 'telemetry_interval': 1})
    runtime = build_spark_runtime(LLMConfig.from_env(), settings, settings=resource_settings)
    service = runtime.manager.get(service_id)
    controller = service.controller
    engine = MockMovieProduction(root / 'project', media_settings=settings,
        event_bus=DurableLocalEventBus(root / 'events'))
    engine.current_project = Project(project_id='project_p4d_audio_smoke_20260909',
        brief=ProjectBrief(title='P4D real audio calibration', logline='Isolated audio smoke',
            story_description='Existing local audio models only', target_duration=30, output_language='zh-CN'))
    runtime.bind(engine.current_project.project_id, engine.event_bus, engine.trace_id)
    phase = 'cold_baseline'
    samples, outputs = [], []
    attempt = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    report = {'service_id': service_id, 'attempt': attempt,
              'bootstrap_calibration': True, 'human_listening': 'pending'}
    log = (root / f'{service_id}-telemetry.jsonl').open('a', encoding='utf-8')
    async def sample():
        snapshot = await runtime.snapshot(engine.current_project.project_id)
        item = {'phase': phase, **snapshot.model_dump(mode='json')}
        samples.append(item)
        log.write(json.dumps(item)+'\n'); log.flush()
        if snapshot.available_unified_memory_bytes < (resource_settings.system_reserve_gib+resource_settings.safety_margin_gib)*GiB:
            await controller.action(service_id, 'stop')
            raise RuntimeError('Calibration stopped: configured unified-memory headroom exhausted')
        return snapshot
    async def monitor():
        while True:
            await sample()
            await asyncio.sleep(1)
    task = None
    try:
        if runtime.active_leases():
            raise RuntimeError('Calibration requires no active or uncertain production leases')
        for managed in runtime.manager.all():
            state = await controller.inspect(managed.descriptor.service_id)
            if state.get('Running'):
                raise RuntimeError('Calibration requires every managed container initially stopped')
            await managed.status()
        cold = await sample()
        report['cold_available_gib'] = cold.available_unified_memory_bytes/GiB
        task = asyncio.create_task(monitor())
        phase = 'startup'
        started = perf_counter()
        await runtime.manager.ensure_ready(service_id, timeout=900)
        report['startup_to_ready_seconds'] = perf_counter()-started
        phase = 'ready'
        ready = await sample()
        endpoint = settings.tts_endpoint if service_id=='tts' else settings.music_endpoint
        async with httpx.AsyncClient(trust_env=False,timeout=10) as client:
            report['health'] = (await client.get(endpoint+'/health')).json()
            report['models'] = (await client.get(endpoint+'/v1/models')).json()
            if service_id == 'music':
                report['model_inventory'] = (await client.get(endpoint+'/v1/model_inventory')).json()
        print(json.dumps({'service': service_id, 'phase': 'ready', 'startup_seconds':report['startup_to_ready_seconds']}), flush=True)
        provider = engine.media_runtime.providers.get('qwen3_tts' if service_id=='tts' else 'ace_step')
        compiler = GenericAudioPromptCompiler()
        async def generate(request):
            nonlocal phase
            existing = engine.artifact_store.get(request.output_artifact_id)
            if existing:
                outputs.append(existing.model_dump(mode='json'))
                return existing
            phase = 'inference:'+request.output_artifact_id
            await sample()
            started = perf_counter()
            response = await provider.generate(request)
            artifact = engine.media_runtime._register_response(
                job=GenerationJob(job_id=request.job_id,project_id=request.project_id,
                    node_id='isolated_calibration',task=request.purpose.value,idempotency_key=request.output_artifact_id),
                response=response,modality=MediaModality.AUDIO,artifact_type=ArtifactType.AUDIO,
                purpose=request.purpose.value,input_ids=[r.artifact_id for r in request.references],
                prompt=request.prompt_package,seed=request.seed,encoding=response.result.encoding,
                duration=response.result.duration_seconds,
                extra={**response.result.provider_metadata,'bootstrap_calibration':True,'sample_rate':response.result.sample_rate,
                       'channels':response.result.channels,'calibration_wall_seconds':perf_counter()-started})
            engine.artifact_store.select(artifact.artifact_id,artifact.version)
            outputs.append(artifact.model_dump(mode='json'))
            phase = 'after_inference'
            await sample()
            print(json.dumps({'artifact_id':artifact.artifact_id,'sha256':artifact.metadata['sha256'],
                'duration':artifact.metadata['duration_seconds'],'latency':artifact.metadata['calibration_wall_seconds']}),flush=True)
            return artifact
        common = {'project_id':engine.current_project.project_id,'seed':42}
        if service_id == 'tts':
            reference_text = '林恩，不要靠近地面。'
            instruction = '一位成年女性，普通话清晰自然，声音沉静、克制，中低音，具有冷静的电影对白质感。'
            anchor = await generate(VoiceDesignRequest(**common,job_id='smoke:anchor',output_artifact_id='smoke_voice_anchor',
                character_id='smoke_character',text=reference_text,language='zh-CN',design_instruction=instruction,
                prompt_package=compiler.compile(AudioPurpose.SPEECH,instruction,[])))
            profile = CharacterVoiceProfile(voice_profile_id='smoke_voice_profile',project_id=common['project_id'],
                character_id='smoke_character',design_instruction=instruction,reference_text=reference_text,
                reference_artifact_id=anchor.artifact_id,reference_artifact_version=anchor.version,
                reference_sha256=anchor.metadata['sha256'],language='zh-CN',provider_id='qwen3_tts',
                model_profile='qwen3-tts-voice-design → qwen3-tts-base')
            (root/'voice-profile.json').write_text(profile.model_dump_json(indent=2),encoding='utf-8')
            ref = MediaReference(reference_type=ReferenceType.VOICE,artifact_id=anchor.artifact_id,
                version=anchor.version,sha256=anchor.metadata['sha256'])
            for identity,text,language in [('zh_1',reference_text,'zh-CN'),
                ('zh_2','留在舱内，等我回来。','zh-CN'),('en','Stay inside the cabin and wait for me.','en')]:
                await generate(SpeechGenerationRequest(**common,job_id='smoke:'+identity,output_artifact_id='smoke_speech_'+identity,
                    character_id='smoke_character',text=text,language=language,voice_profile=profile.voice_profile_id,
                    voice_profile_version=profile.version,reference_voice=ref,references=[ref],reference_text=reference_text,
                    prompt_package=compiler.compile(AudioPurpose.SPEECH,text,[])))
        else:
            prompt='psychological science-fiction underscore, cold electronic ambience, slow pulse, restrained tension, instrumental, no vocals, no lyrics'
            await generate(MusicGenerationRequest(**common,job_id='smoke:music',output_artifact_id='smoke_music',
                mood=prompt,duration_target_seconds=15,prompt_package=compiler.compile(AudioPurpose.MUSIC,prompt,[])))
        if task.done():
            task.result()
        phase='after_inference'
        after=await sample()
        report['oom_killed']=await service.oom_killed()
        if report['oom_killed']: raise RuntimeError('Audio container reports OOM')
        # Exercise P4A's actual wall-clock TTL path; no fabricated idle timestamp.
        phase='ttl_wait'
        await runtime.maintain(engine.current_project.project_id)
        await asyncio.sleep(11)
        phase='ttl_stop'
        await runtime.maintain(engine.current_project.project_id)
        stopped=await controller.inspect(service_id)
        if stopped.get('Running'): raise RuntimeError('TTL did not stop isolated audio service')
        await sample()
        peak=min(samples,key=lambda item:item['available_unified_memory_bytes'])
        report.update(artifacts=outputs,ready_available_gib=ready.available_unified_memory_bytes/GiB,
            after_available_gib=after.available_unified_memory_bytes/GiB,
            sampled_peak_pressure_gib=max(0,cold.available_unified_memory_bytes-peak['available_unified_memory_bytes'])/GiB,
            resident_pressure_gib=max(0,cold.available_unified_memory_bytes-min(ready.available_unified_memory_bytes,after.available_unified_memory_bytes))/GiB,
            peak_is_sampled=True,ttl_stop=stopped,status='passed',samples=samples)
    except BaseException as error:
        report.update(status='failed',error_type=type(error).__name__,error=str(error),artifacts=outputs,samples=samples)
        with suppress(Exception):
            report['container_state']=await controller.inspect(service_id)
            if report['container_state'].get('Running'):
                await controller.action(service_id,'stop')
        raise
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError,Exception): await task
        log.close()
        content=json.dumps(report,ensure_ascii=False,indent=2)
        report_path.write_text(content,encoding='utf-8')
        (root/f'{service_id}-attempt-{attempt}.json').write_text(content,encoding='utf-8')
        runtime.close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-real',action='store_true')
    parser.add_argument('--service',choices=['tts','music'],required=True)
    parser.add_argument('--root',type=Path,default=Path('workspace/p4d-smoke-20260909'))
    args=parser.parse_args()
    if not args.run_real: parser.error('Explicit --run-real is required')
    asyncio.run(run(args.service,args.root))
