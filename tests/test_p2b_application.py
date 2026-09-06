"""P2B boundary extensions preserve legacy context, review and execution policy."""
import asyncio
import httpx
from movie_agent.api.app import create_app
from movie_agent.application.creative_inputs import CreativeHints, SceneSeed, KeyMoment
from movie_agent.application.studio import StudioSnapshot
from movie_agent.orchestration.runtime.context import ContextBuilder
from movie_agent.orchestration.runtime.contracts import RoleId
from tests.p2a_fakes import FakeReasoningProvider
from tests.test_p2a_api import service_at
from tests.test_p2a_runtime import brief


def test_minimal_input_hints_restart_and_legacy_context(tmp_path):
    async def scenario():
        service = service_at(tmp_path, FakeReasoningProvider())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service)), base_url="http://test") as client:
            assert (await client.post('/projects', json={'story_description': '  '})).status_code == 422
            assert (await client.post('/projects', json={'story_description': 'Story', 'creative_hints': {'author': 'model'}})).status_code == 422
            response = await client.post('/projects', json={'story_description': 'A recorded signal arrives early.',
                'creative_hints': {'scene_seeds': [{'title': 'Signal', 'description': 'An archivist listens.'}],
                                   'key_moments': [{'description': 'The clock stops.'}]}})
            assert response.status_code == 201
            record = response.json()
            pid = record['project']['project_id']
            assert record['project']['brief']['target_duration'] == 30
            assert 'creative_hints' not in record['project']['brief']
            snapshot = StudioSnapshot.model_validate((await client.get(f'/projects/{pid}/studio')).json())
            assert snapshot.creative_hints.scene_seeds[0].title == 'Signal'
            assert snapshot.media_mode == 'mock'
            assert snapshot.event_cursor == snapshot.events[-1].event_id
            assert len(snapshot.graph.nodes) == 21
            assert (await client.get('/projects')).json()[0]['project']['project_id'] == pid
        restarted = service_at(tmp_path, FakeReasoningProvider())
        engine = restarted.engine(pid)
        definition = engine.runner.registry.get(RoleId.CREATIVE_PRODUCER)
        context = engine.runner.context_builder.build(definition, engine.current_project)
        assert context.payload['user_authored_creative_hints']['author'] == 'user'
        assert context.payload['user_authored_creative_hints']['key_moments'][0]['description'] == 'The clock stops.'
        # Legacy payloads produce byte-identical role contexts, including version and hash.
        legacy = restarted.create(brief())
        legacy_engine = restarted.engine(legacy.project.project_id)
        assert legacy_engine.runner.context_builder.build(definition, legacy.project) == ContextBuilder().build(definition, legacy.project)
    asyncio.run(scenario())


def test_studio_complete_reads_and_rejected_review_stays_held(tmp_path):
    async def scenario():
        service = service_at(tmp_path / 'complete', FakeReasoningProvider(), auto_approve=True)
        record = service.create(brief(), CreativeHints(scene_seeds=[SceneSeed(title='Opening', description='A signal')], key_moments=[KeyMoment(description='A choice')]))
        pid = record.project.project_id
        service.start(pid)
        await service.tasks[pid]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service)), base_url="http://test") as client:
            snapshot = StudioSnapshot.model_validate((await client.get(f'/projects/{pid}/studio')).json())
            assert snapshot.status == 'completed'
            assert snapshot.project.shots and snapshot.jobs and snapshot.artifacts and snapshot.roles
            assert all(r.committed for r in snapshot.roles)
            assert snapshot.evaluations
            assert (await client.post(f'/projects/{pid}/cancel')).status_code == 409
        held = service_at(tmp_path / 'held', FakeReasoningProvider())
        pid = held.create(brief()).project.project_id
        held.start(pid)
        await held.tasks[pid]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(held)), base_url="http://test") as client:
            review = (await client.get(f'/projects/{pid}/reviews')).json()[0]
            response = await client.post(f"/reviews/{review['review_id']}/resolve", json={'approved': False, 'notes': 'Preserve the ambiguous ending.'})
            assert response.json()['status'] == 'rejected'
            assert (await client.post(f'/projects/{pid}/resume')).status_code == 409
    asyncio.run(scenario())


def test_project_cancel_stops_local_commit_and_is_durable(tmp_path):
    async def scenario():
        class Blocking(FakeReasoningProvider):
            entered = None
            async def submit(self, request):
                self.entered.set()
                await asyncio.Event().wait()
        provider = Blocking()
        provider.entered = asyncio.Event()
        service = service_at(tmp_path, provider)
        pid = service.create(brief()).project.project_id
        service.start(pid)
        await provider.entered.wait()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service)), base_url='http://test') as client:
            assert (await client.post(f'/projects/{pid}/cancel')).json()['status'] == 'cancelled'
            assert (await client.post(f'/projects/{pid}/resume')).status_code == 409
        restored = service_at(tmp_path, FakeReasoningProvider())
        snapshot = restored.snapshot(pid)
        assert snapshot['status'] == 'cancelled'
        assert snapshot['project']['creative_direction'] is None
        assert any(j.cancellation_requested for j in restored.engine(pid)._all_jobs())
        assert all(j.status == 'cancelled' for j in restored.engine(pid)._all_jobs() if j.cancellation_requested)
    asyncio.run(scenario())
