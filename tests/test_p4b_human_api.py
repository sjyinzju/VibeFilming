"""Proactive PASS feedback and existing review API share one durable command path."""

import asyncio
import httpx
import pytest
from movie_agent.api.app import create_app
from movie_agent.application.service import ProductionService
from movie_agent.application.repository import ProductionStatus
from movie_agent.application.studio import studio_snapshot
from movie_agent.storage.projects import LocalProjectRepository
from movie_agent.services.reasoning_production import ReasoningMovieProduction
from movie_agent.execution.durable_events import DurableLocalEventBus
from movie_agent.providers.registry import ProviderFactory, MediaProviderSettings
from movie_agent.media import MediaModality
from tests.p2a_fakes import FakeReasoningProvider
from tests.test_p4b_repair_loop import ControlledVision, FIXTURE


def service_at(root, vision):
    factory=ProviderFactory.defaults()
    factory.register(MediaModality.VISION,'mock',lambda:vision)
    def create(pid):
        return ReasoningMovieProduction(root/pid,FakeReasoningProvider(),real_roles=[],
            media_settings=MediaProviderSettings(),media_provider_factory=factory,
            event_bus=DurableLocalEventBus(root/pid/'events'))
    return ProductionService(LocalProjectRepository(root),create,auto_approve=True)


def command(inspection, disposition='custom_repair'):
    return {'command_id':'feedback-click','inspection_result_id':inspection.result_id,
        'target_artifact_id':inspection.target_artifact_id,'target_artifact_version':inspection.target_artifact_version,
        'target_sha256':inspection.target_sha256,'disposition':disposition,
        'feedback':'  请保留人物和场景。\n运镜更准确。  ',
        'dismissed_issue_ids':[i.issue_id for i in inspection.issues], 'change_requests':['Follow the canonical camera motion']}


def test_proactive_pass_feedback_scoped_revision_sse_and_restart(tmp_path):
    async def scenario():
        vision=ControlledVision(decision='pass')
        # For this scenario all initial versions genuinely pass.
        vision.inspect=__import__('movie_agent.providers.media',fromlist=['MockVisionProvider']).MockVisionProvider().inspect
        service=service_at(tmp_path,vision)
        brief=ReasoningMovieProduction.load_brief(FIXTURE)
        pid=service.create(brief).project.project_id
        engine=service.engine(pid)
        await engine.run(resume=True,stop_after_node='full_film_review')
        record=service.repository.get(pid); record.status=ProductionStatus.PAUSED; service.repository.save(record)
        inspection=next(i for i in engine.media_runtime.inspections if i.shot_id==engine.current_project.shots[0].shot_id)
        assert inspection.decision=='pass'
        canonical=engine.current_project.story_bible.model_dump_json()
        role_jobs=[j.job_id for j in engine._all_jobs() if j.task=='structured_text']
        before=[(a.artifact_id,a.version) for a in engine.artifact_store.list_all() if a.artifact_type.value=='frame']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service)),base_url='http://test') as client:
            invalid=command(inspection); invalid['target_artifact_version']=99
            assert (await client.post(f'/projects/{pid}/media-feedback',json=invalid)).status_code==409
            result=await client.post(f'/projects/{pid}/media-feedback',json=command(inspection))
            assert result.status_code==200,result.text
            rid=result.json()['review_id']
            assert result.json()['gate_type']=='agent_escalation'
            assert (await client.post(f'/projects/{pid}/media-feedback',json=command(inspection))).json()['review_id']==rid
            events=(await client.get(f'/projects/{pid}/events?follow=false')).text
            assert 'human_media_directive_created' in events
        assert engine.current_production.node('shot_production').status=='succeeded'
        assert engine.current_production.node('repair_accept').status=='pending'
        restarted=service_at(tmp_path,vision)
        restarted.start(pid,resume=True); await restarted.tasks[pid]
        new=restarted.engine(pid)
        assert new.current_project.story_bible.model_dump_json()==canonical
        assert len(new.human_media_directives)==1
        assert new.human_media_directives[0].feedback==command(inspection)['feedback']
        assert [(a.artifact_id,a.version) for a in new.artifact_store.list_all() if a.artifact_type.value=='frame']==before
        assert [j.job_id for j in new._all_jobs() if j.task=='structured_text']==role_jobs
        assert len(new.artifact_store.list_versions(inspection.target_artifact_id))==2
        snapshot=studio_snapshot(restarted,pid)
        assert len(snapshot.human_media_directives)==1
        assert snapshot.status=='completed'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(restarted)),base_url='http://test') as client:
            latest=next(i for i in reversed(new.media_runtime.inspections) if i.shot_id==inspection.shot_id)
            body=command(latest);body['command_id']='after-completion'
            assert (await client.post(f'/projects/{pid}/media-feedback',json=body)).status_code==409
    asyncio.run(scenario())
