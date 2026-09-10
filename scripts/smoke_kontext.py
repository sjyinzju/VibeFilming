"""Explicit isolated smoke, preserving source and measured unified-memory evidence."""
import argparse
import asyncio
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter

import httpx
from movie_agent.artifacts.store import LocalArtifactStore
from movie_agent.domain import ArtifactType, GenerationJob, PromptPackage
from movie_agent.media import ImageGenerationRequest, ImageGenerationMode, ImagePurpose, MediaReference, ReferenceType
from movie_agent.media.storage import LocalBinaryArtifactStore
from movie_agent.model_services.resources import ResourceRuntimeSettings
from movie_agent.model_services.spark import SparkDockerServiceController
from movie_agent.providers.registry import MediaProviderSettings
from movie_agent.services.mock_production import MockMovieProduction
from movie_agent.execution.jobs import JobManager


async def main(root):
    root.mkdir(parents=True,exist_ok=True)
    source=Path('workspace/p4d-audio-acceptance/project_3effeb45f3844bf89c2c96e83770114b')
    store=LocalArtifactStore(source/'artifacts')
    binaries=LocalBinaryArtifactStore(source/'artifacts/media')
    frame=next(a for a in store.list_all() if a.artifact_type==ArtifactType.FRAME and a.metadata.get('mock') is False)
    engine=MockMovieProduction(root,media_settings=MediaProviderSettings(image_provider='flux_kontext',kontext_endpoint='http://127.0.0.1:9002'))
    engine.media_runtime.bind_jobs(JobManager(engine.event_bus,engine.trace_id))
    if engine.artifact_store.get('kontext_smoke_edit'):
        print('Committed smoke exists: refusing duplicate edit',flush=True); return
    with binaries.open(frame.uri) as stream: content=stream.read()
    if not engine.artifact_store.get(frame.artifact_id,frame.version):
        saved=engine.binary_store.put(frame.artifact_id,frame.version,content,mime_type='image/png',extension='png')
        engine.artifact_store.register(frame.model_copy(update={'uri':saved.uri}))
    from movie_agent.config import LLMConfig
    from movie_agent.model_services.wiring import build_spark_runtime
    resources=ResourceRuntimeSettings.from_env().model_copy(update={'warm_idle_ttl':10,'telemetry_interval':1})
    config=MediaProviderSettings.from_env().model_copy(update={'kontext_enabled':True})
    runtime=build_spark_runtime(LLMConfig.from_env(),config,settings=resources)
    controller=runtime.manager.get('kontext').controller
    if runtime.active_leases():
        runtime.close(); raise RuntimeError('Isolated calibration requires no active/uncertain production lease')
    for service in runtime.manager.all():
        if (await controller.inspect(service.descriptor.service_id)).get('Running'):
            runtime.close(); raise RuntimeError('All managed containers must be stopped before calibration')
    report={'source':{'artifact_id':frame.artifact_id,'version':frame.version,'sha256':sha256(content).hexdigest()},'samples':[]}
    async def sample(stage):
        memory=await controller.memory()
        report['samples'].append({'stage':stage,'elapsed':perf_counter()-started,**memory})
        if memory['MemAvailable']<12*1024**3:
            await controller.action('kontext','stop')
            raise RuntimeError('Isolated calibration stopped at P4A memory headroom')
    started=perf_counter()
    await controller.action('kontext','stop')
    await sample('cold')
    stage='startup'
    async def monitor():
        while True:
            await sample(stage)
            (root/'telemetry.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            await asyncio.sleep(2)
    observer=asyncio.create_task(monitor())
    try:
        await controller.action('kontext','start')
        async with httpx.AsyncClient(trust_env=False,timeout=5) as client:
            for _ in range(120):
                try:
                    health=(await client.get('http://127.0.0.1:9002/health')).json()
                    if health.get('ready'): break
                except (httpx.HTTPError,ValueError): pass
                if not (await controller.inspect('kontext'))['Running']: raise RuntimeError('Kontext exited during startup')
                await asyncio.sleep(2)
            else: raise TimeoutError('Kontext startup exceeded 240s')
        report['health']=health; report['startup_seconds']=perf_counter()-started
        await sample('ready'); stage='inference'
        request=ImageGenerationRequest(job_id='p5-kontext-smoke',project_id='p5-kontext-smoke',
            mode=ImageGenerationMode.IMAGE_EDIT,purpose=ImagePurpose.FIRST_FRAME,
            source_image=MediaReference(reference_type=ReferenceType.SOURCE_IMAGE,artifact_id=frame.artifact_id,
                version=frame.version,sha256=sha256(content).hexdigest()),
            prompt_package=PromptPackage(compiler_id='p5-smoke',compiler_version='1',
                positive_prompt='Adjust the lighting to a slightly warmer, softer cinematic key light. Preserve the same person, face, age, hairstyle, wardrobe, pose, camera framing, location and all objects. Change only the light color temperature slightly.'),
            width=1024,height=576,aspect_ratio='16:9',seed=51209,output_artifact_id='kontext_smoke_edit',
            provider_parameters={'steps':28,'guidance':2.5})
        from movie_agent.media import MediaCapabilityRequirement
        request.required_capabilities=[MediaCapabilityRequirement(capability='image_edit')]
        # Bootstrap follows existing smoke_audio: isolated ownership/telemetry before a measured lease profile exists.
        from movie_agent.media import MediaModality
        response=await engine.media_runtime.providers.get('flux_kontext').generate(request)
        output=engine.media_runtime._register_response(job=GenerationJob(job_id=request.job_id,project_id=request.project_id,
            task='image',idempotency_key=request.job_id),response=response,modality=MediaModality.IMAGE,
            artifact_type=ArtifactType.FRAME,purpose=request.purpose.value,input_ids=[frame.artifact_id],
            prompt=request.prompt_package,seed=request.seed,dimensions=response.result.dimensions,
            encoding=response.result.encoding,extra={'bootstrap_calibration':True})
        report['output']=output.model_dump(mode='json')
        await sample('after'); stage='idle'
        print('Real Kontext edit committed',output.artifact_id,output.version,flush=True)
        await runtime.maintain(request.project_id)
        await asyncio.sleep(11)
        await runtime.maintain(request.project_id)
        report['ttl_stopped']=not (await controller.inspect('kontext'))['Running']
    except Exception as error:
        report['failure']=str(error)
        raise
    finally:
        observer.cancel()
        try: await observer
        except asyncio.CancelledError: pass
        await controller.action('kontext','stop')
        await sample('stopped')
        report['state']=await controller.inspect('kontext')
        report['wall_seconds']=perf_counter()-started
        (root/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        runtime.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run-real',action='store_true')
    parser.add_argument('--root',type=Path,default=Path('workspace/p5-kontext-smoke'))
    args=parser.parse_args()
    if not args.run_real: parser.error('--run-real is required')
    asyncio.run(main(args.root))
