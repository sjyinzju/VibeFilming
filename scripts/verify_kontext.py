"""Complete the existing smoke with actual P4A TTL and real before/after VLM evidence."""
import argparse
import asyncio
import json
from pathlib import Path
from movie_agent.config import LLMConfig
from movie_agent.domain import GenerationJob, ResourceClass
from movie_agent.execution.jobs import JobManager
from movie_agent.media import MediaReference,ReferenceType,VisionInspectionRequest,VisionInspectionProfile
from movie_agent.model_services.resources import ResourceRuntimeSettings
from movie_agent.model_services.wiring import build_spark_runtime
from movie_agent.providers.registry import MediaProviderSettings
from movie_agent.services.mock_production import MockMovieProduction

async def main():
    root=Path('workspace/p5-kontext-smoke-03')
    report=json.loads((root/'acceptance.json').read_text(encoding='utf-8'))
    settings=MediaProviderSettings.from_env().model_copy(update={'kontext_enabled':True,'image_provider':'flux_direct','vision_provider':'qwen3_vl'})
    runtime=build_spark_runtime(LLMConfig.from_env(),settings,settings=ResourceRuntimeSettings.from_env().model_copy(update={'warm_idle_ttl':10}))
    engine=MockMovieProduction(root,media_settings=settings,runtime_coordinator=runtime)
    engine.media_runtime.bind_jobs(JobManager(engine.event_bus,engine.trace_id))
    try:
        await runtime.manager.ensure_ready('kontext')
        await runtime.maintain('p5-kontext-smoke')
        await asyncio.sleep(11)
        await runtime.maintain('p5-kontext-smoke')
        report['p4a_ttl_state']=await runtime.manager.get('kontext').controller.inspect('kontext')
        assert not report['p4a_ttl_state']['Running']
        output=engine.artifact_store.get('kontext_smoke_edit',1)
        request=VisionInspectionRequest(job_id='p5-kontext-compare',project_id='p5-kontext-smoke',image_artifact_id=output.artifact_id,
            target_artifact_version=1,reference_assets=[MediaReference(reference_type=ReferenceType.SOURCE_IMAGE,**report['source'])],
            profiles=[VisionInspectionProfile.IMAGE_QUALITY,VisionInspectionProfile.PROMPT_ALIGNMENT,VisionInspectionProfile.SCENE_CONSISTENCY],
            expected_requirements=['The source image is the blue/cyan original; the target should make the lighting warmer while preserving '
                'the same people/silhouettes, positions, wardrobe and city arch architecture. State observable changes and mismatches; '
                'faces are not readable, so do not claim verified facial identity.'],output_artifact_id='kontext_before_after_inspection',
            output_language='zh',retry_budget=0)
        result=await engine.media_runtime.inspect(GenerationJob(job_id=request.job_id,project_id=request.project_id,
            task='vision',resource_class=ResourceClass.LIGHT,idempotency_key=request.job_id),request)
        report['vlm']=result.model_dump(mode='json')
        print(json.dumps({'decision':result.decision,'summary':result.summary,'scores':[s.model_dump() for s in result.scores]},ensure_ascii=False),flush=True)
        (root/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    except Exception as error:
        report['failure']=str(error)
        report['jobs']=[j.model_dump(mode='json') for j in engine.media_runtime.job_manager.all()]
        (root/'verification-failure.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps([j.provenance.parameters.get('provider_attempts') for j in engine.media_runtime.job_manager.all()]),flush=True)
        raise
    finally:
        await runtime.maintain('p5-kontext-smoke')
        await asyncio.sleep(11)
        await runtime.maintain('p5-kontext-smoke')
        runtime.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-real',action='store_true');a=p.parse_args()
    if not a.run_real:p.error('--run-real required')
    asyncio.run(main())
