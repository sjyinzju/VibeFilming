"""Deterministic repair, human feedback and checkpoint acceptance; no real inference."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from movie_agent.api.app import create_app
from movie_agent.application.media_commands import resolve_media_directive
from movie_agent.application.repository import ProductionStatus
from movie_agent.application.service import CommandConflict
from movie_agent.domain import EventType, IssueSeverity, JobStatus
from movie_agent.media import (HumanRepairInput, MediaIssue, MediaIssueType, MediaRepairActionType,
    VisionDecision, VisionInspectionResult, VisionScore, VisionInspectionProfile)
from movie_agent.providers.media import MockVisionProvider
from movie_agent.providers.registry import ProviderFactory, MediaProviderSettings
from movie_agent.services import MockMovieProduction
from tests.test_p2a_api import service_at
from tests.p2a_fakes import FakeReasoningProvider

FIXTURE=Path('tests/fixtures/sample_brief.json')


class ControlledVision(MockVisionProvider):
    def __init__(self, decision='human_review', action='rewrite_prompt', always=False):
        super().__init__(); self.decision=decision; self.action=action; self.always=always; self.calls=[]
    async def inspect(self, request):
        self.calls.append((request.video_artifact_id,request.target_artifact_version))
        fails=(self.always or request.target_artifact_version==1) and request.shot_id.endswith('001')
        return VisionInspectionResult(request_id=request.request_id, target_artifact_id=request.video_artifact_id,
            provider_id=self.provider_id, scores=[VisionScore(profile=p,score=.6 if fails else .95) for p in request.profiles],
            evidence=['The hand remains below the cup at the intended action end.'],
            issues=[MediaIssue(issue_type='action_incomplete',severity='major',message='Complete the cup lift.',
                evidence=['Cup remains on the table.'],time_ranges=[{'start_seconds':1,'end_seconds':2}],
                suggested_action=self.action)] if fails else [],
            decision=self.decision if fails else 'pass', summary='Action incomplete.' if fails else 'Shot passed.')


def engine_at(path, provider):
    factory=ProviderFactory.defaults()
    from movie_agent.media import MediaModality
    factory.register(MediaModality.VISION,'mock',lambda:provider)
    return MockMovieProduction(path,media_settings=MediaProviderSettings(),media_provider_factory=factory)


def command_for(engine, disposition, **changes):
    review=engine.human_gates.pending_for_node('repair_accept')
    inspection=next(i for i in engine.media_runtime.inspections if i.result_id==review.inspection_result_id)
    command=HumanRepairInput(command_id='user-action-1',inspection_result_id=inspection.result_id,
        target_artifact_id=inspection.target_artifact_id,target_artifact_version=inspection.target_artifact_version,
        target_sha256=inspection.target_sha256,disposition=disposition,**changes)
    return review,command


@pytest.mark.parametrize('disposition',['keep_current','apply_ai_repair','regenerate','custom_repair'])
def test_four_human_actions_restart_and_verbatim_feedback(tmp_path,disposition):
    async def scenario():
        provider=ControlledVision()
        engine=engine_at(tmp_path,provider)
        result=await engine.run(engine.load_brief(FIXTURE))
        assert not result.completed
        assert engine.current_production.node('repair_accept').status=='waiting_human'
        assert len(engine.human_gates.all())==3  # Story, Shot, media escalation
        original=engine.current_project.story_bible.model_dump_json()
        pending_id=engine.human_gates.pending_for_node('repair_accept').review_id
        engine=engine_at(tmp_path,provider)
        project,graph=engine._restore(); engine.current_project,engine.current_production=project,graph
        assert engine.human_gates.pending_for_node('repair_accept').review_id==pending_id
        review,command=command_for(engine,disposition,feedback='  保留停顿。\n请完成举杯动作。  ',
            preserve_requirements=['Preserve face and wardrobe'],change_requests=['Complete the existing action'])
        resolved=resolve_media_directive(None,engine,review,command)
        assert resolved.status=='approved'
        assert resolve_media_directive(None,engine,review,command).directive_id==resolved.directive_id
        assert len(engine.human_media_directives)==1
        # Crash/restart after the directive commit, before any repair job exists.
        engine=engine_at(tmp_path,provider)
        result=await engine.run(resume=True)
        assert result.completed and engine.current_project.story_bible.model_dump_json()==original
        directive=engine.human_media_directives[0]
        assert directive.feedback==command.feedback
        versions=engine.artifact_store.list_versions('video_shot_001')
        if disposition=='keep_current':
            assert len(versions)==1 and versions[0].selected
            assert engine.human_overrides[0]['ai_decision']=='human_review'
            assert engine.media_runtime.inspections[0].decision=='human_review'
        else:
            assert len(versions)==2 and versions[-1].selected
            context=versions[-1].provenance.parameters['repair_context']
            assert context['raw_feedback']==command.feedback
            assert versions[-1].provenance.repair_plan_ids
            prompt=engine.artifact_store.get('prompt_shot_001')
            from urllib.parse import urlparse,unquote
            path=unquote(urlparse(prompt.uri).path).lstrip('/') if __import__('os').name=='nt' else unquote(urlparse(prompt.uri).path)
            text=Path(path).read_text(encoding='utf-8')
            assert '[fix]' in text and 'human_feedback' in text
        assert len([x for x in provider.calls if x==('video_shot_001',1)])==1
        assert engine.artifact_store.get(directive.directive_id).uri.startswith('artifact://')
    asyncio.run(scenario())


def test_bounded_auto_repair_uses_changed_prompt_and_preserves_history(tmp_path):
    async def scenario():
        provider=ControlledVision(decision='repair')
        engine=engine_at(tmp_path,provider)
        result=await engine.run(engine.load_brief(FIXTURE))
        assert result.completed
        assert ('video_shot_001',2) in provider.calls
        assert len(engine.artifact_store.list_versions('video_shot_001'))==2
        assert [i.decision.value for i in engine.media_runtime.inspections if i.shot_id=='shot_001']==['repair','pass']
        repair=engine.artifact_store.get('video_shot_001',2)
        assert 'Complete the cup lift.' in json.dumps(repair.provenance.parameters['repair_context'])
        assert repair.provenance.parameters['repair_context']['targeted_time_ranges'][0]['start_seconds'] == 1
        assert len(engine.human_gates.all())==3  # Only the original Story/Shot/Final gates.
        shot = result.project.shots[0]
        shot.reference_artifact_ids.append('missing-reference')
        with pytest.raises(ValueError, match='media reference unavailable'):
            engine._media_references(result.project, shot)
    asyncio.run(scenario())


@pytest.mark.parametrize('action',['image_edit','strengthen_character_reference','split_shot'])
def test_unsupported_repairs_escalate_without_generation(tmp_path,action):
    async def scenario():
        engine=engine_at(tmp_path,ControlledVision(decision='repair',action=action))
        result=await engine.run(engine.load_brief(FIXTURE))
        assert not result.completed and engine.human_gates.pending_for_node('repair_accept')
        assert len(engine.artifact_store.list_versions('video_shot_001'))==1
        assert engine.media_repair_plans[0].unsupported_actions==[action]
    asyncio.run(scenario())


def test_retry_exhaustion_is_persistent_human_gate(tmp_path):
    async def scenario():
        engine=engine_at(tmp_path,ControlledVision(decision='repair',always=True))
        result=await engine.run(engine.load_brief(FIXTURE))
        shot=next(s for s in result.project.shots if s.shot_id=='shot_001')
        assert not result.completed
        assert len(engine.artifact_store.list_versions('video_shot_001'))==shot.retry_budget+1
        assert engine.media_repair_plans[-1].exhausted
    asyncio.run(scenario())


def test_resume_after_video_v2_commit_does_not_regenerate(tmp_path):
    async def scenario():
        provider=ControlledVision(decision='repair')
        engine=engine_at(tmp_path,provider)
        original=engine._vision_evaluation
        async def crash(project,shot,video):
            if video.version==2: raise OSError('simulated process interruption after video commit')
            return await original(project,shot,video)
        engine._vision_evaluation=crash
        with pytest.raises(OSError):await engine.run(engine.load_brief(FIXTURE))
        first_v2=engine.artifact_store.get('video_shot_001',2).uri
        restarted=engine_at(tmp_path,provider)
        assert (await restarted.run(resume=True)).completed
        assert len(restarted.artifact_store.list_versions('video_shot_001'))==2
        assert restarted.artifact_store.get('video_shot_001',2).uri==first_v2
        assert sum(x==('video_shot_001',2) for x in provider.calls)==1
    asyncio.run(scenario())


def test_frame_repair_changes_frame_then_video(tmp_path):
    async def scenario():
        engine=engine_at(tmp_path,ControlledVision(decision='repair',action='regenerate_last_frame'))
        result=await engine.run(engine.load_brief(FIXTURE))
        assert result.completed
        assert len(engine.artifact_store.list_versions('frame_shot_001_last'))==2
        video=engine.artifact_store.get('video_shot_001',2)
        assert next(r for r in video.provenance.parameters['input_artifact_versions'] if r['artifact_id']=='frame_shot_001_last')['version']==2
        old=next(i for i in result.media_inspections if i.shot_id=='shot_001' and i.target_artifact_version==1)
        assert 'artifact://frame_shot_001_last/v1' in old.provenance.parameters['input_artifact_uris']
    asyncio.run(scenario())
