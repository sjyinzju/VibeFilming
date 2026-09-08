"""Explicit opt-in browser acceptance server; never imported by production."""

import asyncio
import os
from pathlib import Path
from fastapi import HTTPException
from scripts.accept_vision import SOURCE, ROOT, prepare_acceptance, inspect_existing

if os.environ.get('MOVIE_AGENT_RUN_VISION_INTEGRATION')!='1':
    raise RuntimeError('Real VLM acceptance requires explicit opt-in')
project_id=prepare_acceptance()
os.environ['MOVIE_AGENT_WORKSPACE']=str(ROOT.resolve())
os.environ['MOVIE_AGENT_VISION_PROVIDER']='qwen3_vl'
from movie_agent.api.app import create_app
app=create_app()
task=None


@app.post('/p4b/inspect-existing-video')
async def inspect_video():
    global task
    if task and not task.done():raise HTTPException(409,'Inspection already active')
    task=asyncio.create_task(inspect_existing(app.state.production_service,project_id,root=ROOT,inspection_revision=2))
    return {'project_id':project_id,'status':'running'}


@app.get('/p4b/acceptance')
async def acceptance():
    if task and not task.done():
        return {'status':'running'}
    if task and task.done() and task.exception():
        return {'status':'failed','error_type':type(task.exception()).__name__}
    import json
    report=ROOT/'acceptance.json'
    return json.loads(report.read_text(encoding='utf-8')) if report.exists() else {'status':'running'}


@app.post('/p4b/human-contract')
async def prove_human_contract():
    """Validate a real result through the existing gate; do not resume generation."""
    from movie_agent.media import HumanRepairInput
    service=app.state.production_service
    engine=service.engine(project_id)
    result=next(i for i in reversed(engine.media_runtime.inspections) if i.provider_id=='qwen3_vl')
    canonical=engine.current_project.story_bible.model_dump_json()
    jobs={j.job_id for j in engine._all_jobs()}
    directive=HumanRepairInput(command_id='p4b-contract-'+result.result_id,
        inspection_result_id=result.result_id, target_artifact_id=result.target_artifact_id,
        target_artifact_version=result.target_artifact_version, target_sha256=result.target_sha256,
        disposition='keep_current', feedback='P4B contract test fixture only; do not resume generation.')
    review=service.media_feedback(project_id,directive)
    committed=next(d for d in engine.human_media_directives if d.directive_id==review.directive_id)
    assert engine.current_project.story_bible.model_dump_json()==canonical
    assert {j.job_id for j in engine._all_jobs()}==jobs
    proof={'review':review.model_dump(mode='json'),'directive':committed.model_dump(mode='json'),
           'artifact_uri':engine.artifact_store.get(committed.directive_id).uri,'new_inference_jobs':0}
    import json
    (ROOT/'human-contract.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    return proof
