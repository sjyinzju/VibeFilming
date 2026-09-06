import asyncio
from movie_agent.domain import ProviderResult
from movie_agent.orchestration.runtime.context import content_hash
from movie_agent.orchestration.runtime.budgets import planning_output_budget
from movie_agent.application.studio import studio_snapshot
from tests.p2a_fakes import FakeReasoningProvider
from tests.test_p2a_api import service_at
from tests.test_p2a_runtime import brief


def test_director_uncertain_waits_for_explicit_resume_and_preserves_predecessors(tmp_path):
    class OnceUncertain(FakeReasoningProvider):
        requests = None
        async def submit(self, request):
            self.requests.append(request.model_copy(deep=True))
            name = request.generation_request.parameters['response_format']['json_schema']['name']
            if name == 'ScenePlan' and sum(r.generation_request.parameters['response_format']['json_schema']['name'] == name for r in self.requests) == 1:
                return ProviderResult(provider_request_id=request.provider_request_id, success=False,
                    error_type='remote_completion_uncertain', retryable=False)
            if name == 'ScenePlan':
                assert request.generation_request.parameters['retry_uncertain'] is True
            return await super().submit(request)

    async def scenario():
        provider = OnceUncertain()
        provider.requests = []
        service = service_at(tmp_path, provider, auto_approve=True)
        pid = service.create(brief()).project.project_id
        service.start(pid)
        await service.tasks[pid]
        before = studio_snapshot(service, pid)
        assert before.failure_code == 'remote_completion_uncertain'
        assert len(provider.requests) == 5
        committed = {r.invocation.role_id: content_hash(r.output) for r in before.roles if r.committed}
        assert len(committed) == 4
        await asyncio.sleep(0)
        assert len(provider.requests) == 5  # No automatic retry after the task settles.
        service.start(pid, resume=True)
        await service.tasks[pid]
        after = studio_snapshot(service, pid)
        assert after.status == 'completed'
        for role in after.roles:
            if role.invocation.role_id in committed:
                assert content_hash(role.output) == committed[role.invocation.role_id]
        names = [r.generation_request.parameters['response_format']['json_schema']['name'] for r in provider.requests]
        assert names == ['CreativeDirection', 'StoryBible', 'Screenplay', 'VisualBible', 'ScenePlan', 'ScenePlan', 'ShotPlanDraft']
        director = next(r for r in after.roles if r.invocation.role_id == 'director')
        assert len(director.inference_records) == 2
        assert director.inference_records[0].provider_outcome == 'remote_completion_uncertain'
        assert len(director.attempts) == 1
        assert director.inference_records[1].total_timeout == 360
        assert director.inference_records[1].streaming
    asyncio.run(scenario())


def test_output_budget_scales_with_plan_without_changing_contracts(tmp_path):
    from movie_agent.services.reasoning_production import ReasoningMovieProduction
    from movie_agent.domain import ScenePlan, ShotPlan
    production = ReasoningMovieProduction(tmp_path, FakeReasoningProvider())
    p = asyncio.run(production.run(brief(), stop_after_node='shot_planning')).project
    schemas = [ScenePlan.model_json_schema(), ShotPlan.model_json_schema()]
    small = planning_output_budget('screenwriter', p)
    p.brief.target_duration = 180
    p.brief.max_shots = 24
    assert small < planning_output_budget('screenwriter', p) <= 8192
    first = planning_output_budget('director', p)
    p.screenplay.scenes *= 3
    assert first < planning_output_budget('director', p) <= 8192
    assert planning_output_budget('creative_producer', p) is None
    assert [ScenePlan.model_json_schema(), ShotPlan.model_json_schema()] == schemas
