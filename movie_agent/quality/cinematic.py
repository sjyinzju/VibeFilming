"""Qwen judges cinematic intent from OBSERVED VLM evidence; never pretends to see media."""
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from movie_agent.domain import (Evaluation, EvaluationLayer, EvaluationIssue, EvaluationIssueType,
    IssueSeverity, GenerationJob, GenerationRequest, GenerationStrategy, GenerationStrategyType,
    PromptPackage, ProviderRequest, Provenance, ResourceClass, ProviderResult)
from movie_agent.providers.openai_compatible import CompletionOptions
from movie_agent.quality.budget import fingerprint


class CinematicDimensions(BaseModel):
    model_config=ConfigDict(extra='forbid')
    composition: float | None = Field(default=None,ge=0,le=1)
    visual_hierarchy: float | None = Field(default=None,ge=0,le=1)
    emotional_clarity: float | None = Field(default=None,ge=0,le=1)
    performance_readability: float | None = Field(default=None,ge=0,le=1)
    shot_motivation: float | None = Field(default=None,ge=0,le=1)
    continuity: float | None = Field(default=None,ge=0,le=1)
    editing_usefulness: float | None = Field(default=None,ge=0,le=1)


class CinematicProposal(BaseModel):
    model_config=ConfigDict(extra='forbid')
    score: float = Field(ge=0,le=1)
    summary: str
    evidence_ids: list[str] = Field(min_length=1)
    action: Literal['KEEP','TRIM','REORDER','REPAIR_VISUAL','REGENERATE_VIDEO','HUMAN_REVIEW']
    rationale: str
    major_continuity_break: bool
    dimensions: CinematicDimensions


async def evaluate_cinematic(engine, project, *, shot=None, artifact=None, proxy=None, minimum=.75):
    if artifact:
        evidence=[i for i in engine.media_runtime.inspections if i.target_artifact_id==artifact.artifact_id
                  and i.target_artifact_version==artifact.version
                  and (artifact.metadata.get('sha256') is None or i.target_sha256==artifact.metadata['sha256'])]
        if not evidence: raise ValueError('Cinematic evaluation requires real observed evidence first')
        context={'shot':shot.model_dump(mode='json'), 'story':project.story_bible.model_dump(mode='json'),
                 'director_intent':project.creative_direction.model_dump(mode='json'),
                 'neighbors':[s.model_dump(mode='json') for s in project.shots if s.shot_id in {shot.previous_shot_id,shot.next_shot_id}],
                 'observed_evidence':[i.model_dump(mode='json') for i in evidence]}
        ids={i.result_id for i in evidence}
    else:
        context=proxy
        ids=set(proxy['evidence_ids'])
        if not ids: raise ValueError('Film proxy lacks observed evidence')
    schema=CinematicProposal.model_json_schema()
    schema['properties']['evidence_ids']['items']['enum']=sorted(ids)
    key=fingerprint({'context':context,'minimum':minimum,'schema':schema,'output_language':project.brief.output_language,
        'model':getattr(getattr(engine.llm_provider,'config',None),'model',engine.llm_provider.provider_id)})
    aid='cinematic_'+key[:24]
    previous=engine.artifact_store.get(aid)
    if previous:
        return Evaluation.model_validate(engine.artifact_store.read_structured(previous)['evaluation'])
    request=ProviderRequest(provider_request_id=aid,provider_id=engine.llm_provider.provider_id,
        generation_request=GenerationRequest(job_id=aid,task='structured_text',
            strategy=GenerationStrategy(strategy_type=GenerationStrategyType.STRUCTURED_TEXT,reason='cinematic evidence aggregation'),
            prompt_package=PromptPackage(compiler_id='cinematic-p5',compiler_version='1',positive_prompt=json.dumps(context,ensure_ascii=False)),
            requested_output_type='structured_text',resource_class=ResourceClass.LIGHT,
            parameters=CompletionOptions(system_prompt=(
                'You are the cinematic critic. You cannot see or hear media. Use ONLY the supplied OBSERVED VLM evidence '
                'and canonical story/shot context. Evaluate composition, pacing, emotional clarity, shot usefulness, '
                'continuity and redundancy. Cite supplied evidence IDs. Minor composition/motion imperfections are '
                'non-blocking. Flag major continuity only with evidence. Do not invent WER, listening, lip-sync or '
                'complete frame-by-frame understanding. Recommend a bounded action; never mutate timeline. '
                'Provide all seven dimension scores in 0..1; use null when observed evidence is insufficient. '
                'Write summary and rationale in the project output language: '+project.brief.output_language+'.'),
                response_format={'type':'json_schema','json_schema':{'name':'cinematic_proposal','strict':True,
                    'schema':schema}},max_tokens=3000).model_dump(mode='json')))
    job=GenerationJob(job_id=aid,project_id=project.project_id,task='structured_text',node_id='cinematic_critic',
        provider_id=engine.llm_provider.provider_id,idempotency_key=aid,resource_class=ResourceClass.LIGHT)
    proposal=None
    async def invoke(active):
        nonlocal proposal
        result=await engine.llm_provider.submit(request)
        if not result.success: return result
        try:
            if result.metadata.get('finish_reason')!='stop':
                raise ValueError('cinematic response finish_reason='+str(result.metadata.get('finish_reason')))
            proposal=CinematicProposal.model_validate_json(result.metadata['content'])
            if not set(proposal.evidence_ids)<=ids: raise ValueError('cinematic critic cited unknown evidence')
        except ValueError as error:
            from movie_agent.domain import ProviderErrorType
            engine.artifact_store.create_structured(aid+'_diagnostic',{'input_fingerprint':key,
                'finish_reason':result.metadata.get('finish_reason'),'usage':result.metadata.get('usage'),
                'diagnostic':str(error)[:1200],'remote_request_id':result.metadata.get('remote_request_id'),
                'cited_ids':proposal.evidence_ids if proposal else [],'allowed_ids':sorted(ids),
                'response_received':True},metadata={'purpose':'cinematic_validation_diagnostic'})
            return ProviderResult(provider_request_id=aid,success=False,error_type=ProviderErrorType.GENERATION_FAILED,
                error_message='Completed cinematic response failed local validation',metadata={'request_dispatched':True})
        return result
    coordinator=engine.media_runtime.runtime_coordinator
    result=await coordinator.execute(job,invoke) if coordinator else await invoke(job)
    if not result.success or proposal is None: raise RuntimeError('cinematic reasoning failed: '+str(result.error_type))
    issues=[]
    if proposal.major_continuity_break:
        issues=[EvaluationIssue(issue_type=EvaluationIssueType.CONTINUITY_CONFLICT,severity=IssueSeverity.MAJOR,
            message=proposal.rationale,evidence=proposal.evidence_ids,shot_id=shot.shot_id if shot else None)]
    evaluation=Evaluation(evaluation_id='evaluation_'+key[:24],layer=EvaluationLayer.CINEMATIC,
        target_artifact_id=artifact.artifact_id if artifact else 'rough_cut',
        target_artifact_version=artifact.version if artifact else proxy.get('rough_cut_version'),
        target_shot_id=shot.shot_id if shot else None,score=proposal.score,
        passed=not issues and proposal.score>=minimum and proposal.action not in {'HUMAN_REVIEW','REPAIR_VISUAL','REGENERATE_VIDEO'},
        issues=issues,summary=proposal.summary)
    engine.artifact_store.create_structured(aid,{'evaluation':evaluation.model_dump(mode='json'),
        'proposal':proposal.model_dump(mode='json'),'input_fingerprint':key},
        source_job_id=aid,
        metadata={'purpose':'cinematic_evaluation','mock':False,'evidence_only':True},
        provenance=Provenance(provider_id=engine.llm_provider.provider_id,tool='cinematic_critic',
                              project_id=project.project_id,shot_id=shot.shot_id if shot else None,
                              parameters={'evidence_ids':sorted(ids),'no_direct_media_access':True}))
    return evaluation


def film_proxy(project, reports, timeline, inspections, *, max_shots=14):
    if len(reports)>max_shots: raise ValueError('Film review proxy exceeds configured shot bound')
    ids={sid for r in reports for sid in r.inspection_ids}
    evidence=[i for i in inspections if i.result_id in ids]
    return {'review_scope':'bounded sampled shot evidence, not exhaustive film perception',
        'story':project.story_bible.model_dump(mode='json') if project.story_bible else None,
        'timeline':timeline.model_dump(mode='json') if timeline else None,
        'shot_summaries':[r.model_dump(mode='json') for r in reports],
        'scene_boundaries':[s.model_dump(mode='json') for s in project.scenes],
        'selected_keyframes':[{'inspection_id':i.result_id,'sampling':i.provider_metadata.get('sampling')} for i in evidence],
        'observed_evidence':[{'id':i.result_id,'evidence':i.evidence,'summary':i.summary} for i in evidence],
        'evidence_ids':sorted(ids),'audio_technical_facts':{
            'planned_tracks':[{'name':t.name,'cue_count':len(t.cues),
                'exact_audio_artifacts':[{'artifact_id':c.artifact_id,'version':c.version,'sha256':c.sha256}
                    for c in t.cues]} for t in timeline.audio_tracks] if timeline else [],
            'listening':'pending; track existence does not prove intelligibility, speaker likeness or overlap'}}
