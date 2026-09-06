"""Terminal ownership, local repair, immutable history and finite revision regressions."""
import asyncio
import json
import pytest
from movie_agent.domain import CharacterState, ProviderResult
from movie_agent.orchestration.runtime.cinematographer_drafts import ShotPlanDraft
from movie_agent.orchestration.runtime.context import content_hash
from movie_agent.orchestration.runtime.runner import RoleOutputInvalid
from movie_agent.orchestration.runtime.terminal import terminal_delta, merge_terminal_repair
from movie_agent.services.reasoning_production import ReasoningMovieProduction
from tests.p2a_fakes import FakeReasoningProvider
from tests.test_p2a_runtime import brief


class TerminalProvider(FakeReasoningProvider):
    def __init__(self, *, broken=False):
        super().__init__()
        self.broken = broken
        self.requests = []

    async def submit(self, request):
        self.requests.append(request)
        if request.generation_request.parameters['response_format']['json_schema']['name'] != 'TerminalRepairDraft':
            return await super().submit(request)
        self.calls.append('TerminalRepairDraft')
        prompt = json.loads(request.generation_request.prompt_package.positive_prompt)
        shot = prompt['previous_shot']
        shot['local_state_delta']['character_updates'] = [{'character_id': 'person',
            'emotion': 'safe' if self.broken else 'protective'}]
        return ProviderResult(provider_request_id=request.provider_request_id, success=True,
            metadata={'content': json.dumps({'shot_repairs': [{'shot_index': prompt['shot_index'],
                **{k: shot[k] for k in ('performances', 'local_state_delta', 'frame_planning')}}]})})


def exhausted(workspace):
    engine = ReasoningMovieProduction(workspace, FakeReasoningProvider())
    asyncio.run(engine.run(brief(), stop_after_node='scene_planning'))
    scene = engine.current_project.scenes[0]
    scene.initial_state.character_states['person'] = CharacterState(character_id='person', emotion='focused')
    scene.expected_final_state.character_states['person'] = CharacterState(character_id='person', emotion='protective')
    engine._save_checkpoint(engine.current_project, engine.current_production)
    with pytest.raises(RoleOutputInvalid):
        asyncio.run(engine.run(resume=True))
    return engine


def test_semantic_revision_is_local_audited_and_resume_safe(tmp_path):
    engine = exhausted(tmp_path)
    old = engine.role_results['cinematographer:scene_a'].model_dump(mode='json')
    hashes = {k: content_hash(v.output) for k,v in engine.role_results.items() if v.committed}
    engine.authorize_semantic_revision('scene_a', 'explicit user authorization')
    assert engine.role_revision_history[-1].model_dump(mode='json') == old
    revision = engine.role_results['cinematographer:scene_a'].invocation.semantic_revision
    assert revision.kind == 'SEMANTIC_CONTRACT_REVISION'
    assert revision.parent_result_hash == content_hash(old)
    provider = TerminalProvider()
    restarted = ReasoningMovieProduction(tmp_path, provider)
    result = asyncio.run(restarted.run(resume=True))
    assert result.completed and provider.calls == ['TerminalRepairDraft']
    saved = restarted.role_results['cinematographer:scene_a']
    assert saved.committed and saved.output['shots'][-1]['expected_state_after']['character_states']['person']['emotion'] == 'protective'
    assert {k: content_hash(restarted.role_results[k].output) for k in hashes} == hashes
    assert restarted.role_revision_history[-1].model_dump(mode='json') == old
    assert saved.attempts[0].raw_output and saved.attempts[0].request_prompt
    olddraft = ShotPlanDraft.model_validate_json(old['pending_output'])
    assert saved.output['shots'][0]['camera'] == olddraft.shots[0].camera.model_dump(mode='json')
    with pytest.raises(ValueError):
        restarted.authorize_semantic_revision('scene_a', 'again')


def test_semantic_revision_has_no_implicit_extra_allowance(tmp_path):
    engine = exhausted(tmp_path)
    engine.authorize_semantic_revision('scene_a', 'explicit')
    provider = TerminalProvider(broken=True)
    engine = ReasoningMovieProduction(tmp_path, provider)
    for _ in range(2):
        with pytest.raises(RoleOutputInvalid):
            asyncio.run(engine.run(resume=True))
    assert provider.calls == ['TerminalRepairDraft'] * 3
    with pytest.raises(ValueError):
        engine.authorize_semantic_revision('scene_a', 'again')


def test_delta_uses_entity_identity_and_never_mutates_canon(tmp_path):
    engine = exhausted(tmp_path)
    scene = engine.current_project.scenes[0]
    before = scene.model_dump(mode='json')
    constraint = terminal_delta(scene, scene.initial_state)
    emotion = next(r for r in constraint.requirements if r.field == 'emotion')
    assert (emotion.observed, emotion.required, emotion.entity_id) == ('focused', 'protective', 'person')
    assert emotion.editable_path == 'shots[last].local_state_delta.character_updates[character_id=person].emotion'
    assert scene.model_dump(mode='json') == before
    raw = engine.role_results['cinematographer:scene_a'].pending_output
    shot = json.loads(raw)['shots'][0]
    with pytest.raises(ValueError, match='final shot'):
        merge_terminal_repair(raw, json.dumps({'shot_repairs': [{'shot_index': 9,
            **{k: shot[k] for k in ('performances','local_state_delta','frame_planning')}}]}))


def test_revision_rejects_tampered_source_before_inference(tmp_path):
    engine = exhausted(tmp_path)
    engine.authorize_semantic_revision('scene_a', 'explicit')
    saved = engine.role_results['cinematographer:scene_a']
    saved.terminal_repair_base += ' '
    engine._save_checkpoint(engine.current_project, engine.current_production)
    provider = TerminalProvider()
    restarted = ReasoningMovieProduction(tmp_path, provider)
    with pytest.raises(ValueError, match='source changed'):
        asyncio.run(restarted.run(resume=True))
    assert not provider.calls


def test_api_exhaustion_requires_explicit_revision_and_survives_refresh(tmp_path):
    import httpx
    from movie_agent.api.app import create_app
    from movie_agent.application.repository import ProjectRecord, ProductionStatus
    from tests.test_p2a_api import service_at

    engine = exhausted(tmp_path / 'production')
    pid = engine.current_project.project_id
    provider = TerminalProvider()
    service = service_at(tmp_path, provider, auto_approve=True)
    # The API fixture uses the same engine and durable workspace as the failure.
    engine.llm_provider = provider
    engine.runner.provider = provider
    service.engines[pid] = engine
    service.repository.save(ProjectRecord(project=engine.current_project, status=ProductionStatus.FAILED))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service)), base_url='http://test') as client:
            prefix = '/projects/' + pid
            assert (await client.get(prefix + '/studio')).json()['terminal_revision_scene_id'] == 'scene_a'
            assert (await client.post(prefix + '/resume')).status_code == 409
            assert not provider.calls
            for body in ({'scene_id':'wrong','authorization_reference':'explicit'},
                         {'scene_id':'scene_a','authorization_reference':' '}):
                assert (await client.post(prefix + '/revise-terminal', json=body)).status_code == 409
            command = {'scene_id':'scene_a','authorization_reference':'explicit UI correction action'}
            assert (await client.post(prefix + '/revise-terminal', json=command)).status_code == 202
            await service.tasks[pid]
            response = (await client.get(prefix + '/studio')).json()
            assert response['status'] == 'completed' and response['terminal_revision_scene_id'] is None
            assert (await client.post(prefix + '/revise-terminal', json=command)).status_code == 409
            assert provider.calls == ['TerminalRepairDraft']
    asyncio.run(scenario())
