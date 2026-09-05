"""Incremental role integration, semantic rejection, durability and inference reuse."""

import asyncio
import json
import pytest
from movie_agent.domain import Project, ProjectBrief, CreativeDirection, EventType, Screenplay, ScenePlan, ShotPlan
from movie_agent.orchestration.runtime.contracts import RoleId
from movie_agent.orchestration.runtime.registry import RoleRegistry
from movie_agent.orchestration.runtime.context import ContextBuilder
from movie_agent.orchestration.runtime.validation import RoleOutputValidator
from movie_agent.services.reasoning_production import ReasoningMovieProduction
from movie_agent.execution.durable_events import DurableLocalEventBus
from tests.p2a_fakes import FakeReasoningProvider


def brief():
    return ProjectBrief(title="Signal", logline="A signal stops", story_description="Isolation and choice",
                        target_duration=12, max_shots=3, must_preserve=["Truth wins"], prohibited_elements=["weapons"])


@pytest.mark.parametrize("last", list(RoleId))
def test_incremental_role_slices_complete(tmp_path, last):
    provider = FakeReasoningProvider()
    roles = list(RoleId)[:list(RoleId).index(last) + 1]
    production = ReasoningMovieProduction(tmp_path, provider, real_roles=roles)
    result = asyncio.run(production.run(brief()))
    assert result.completed and result.final_artifact_id == "final_film"
    assert len(provider.calls) == len(roles)
    assert all(r.committed for r in production.role_results.values())
    assert len([j for j in result.jobs if j.task == "structured_text"]) == len(roles)
    assert result.project.creative_direction == CreativeDirection.model_validate(
        production.role_results[RoleId.CREATIVE_PRODUCER].output)


def test_context_filtering_hash_and_constraints():
    p = Project(brief=brief())
    definition = RoleRegistry().get(RoleId.CREATIVE_PRODUCER)
    builder = ContextBuilder()
    a = builder.build(definition, p)
    p.creative_memory.preferences = ["unrelated"]
    p.generation_history.entries = []
    assert a == builder.build(definition, p)
    assert "generation_history" not in a.payload and "jobs" not in a.payload
    p.brief.user_constraints = ["No cuts"]
    assert a.context_hash != builder.build(definition, p).context_hash


def test_semantic_constraint_and_prohibition():
    p = Project(brief=brief())
    bad = CreativeDirection(premise_expansion="He loads weapons", emotional_arc="fear", tone="tense")
    report = RoleOutputValidator().validate(bad, p)
    assert len(report.issues) == 2
    assert {i.code.value for i in report.issues} == {"constraint_violation"}


def test_restart_skips_successful_roles_and_retries_only_failed_node(tmp_path):
    async def scenario():
        first_provider = FakeReasoningProvider(fail_schema="Screenplay")
        first = ReasoningMovieProduction(tmp_path, first_provider,
            event_bus=DurableLocalEventBus(tmp_path / "events"))
        with pytest.raises(Exception, match="disconnect"):
            await first.run(brief())
        assert first_provider.calls == ["CreativeDirection", "StoryBible", "Screenplay"]
        second_provider = FakeReasoningProvider()
        second = ReasoningMovieProduction(tmp_path, second_provider,
            event_bus=DurableLocalEventBus(tmp_path / "events"))
        result = await second.run(resume=True)
        assert result.completed
        assert second_provider.calls == ["Screenplay", "VisualBible", "ScenePlan", "ShotPlanDraft"]
        assert second.event_bus.events(EventType.PROVIDER_REQUEST_STARTED)
        assert len(second.role_results) == 6
    asyncio.run(scenario())


def test_unknown_entity_and_duration_rejected(tmp_path):
    production = ReasoningMovieProduction(tmp_path, FakeReasoningProvider())
    result = asyncio.run(production.run(brief(), stop_after_node="shot_planning"))
    p = result.project
    plan = ShotPlan.model_validate(production.role_results["cinematographer:scene_a"].output)
    plan.shots[0].duration_seconds = 100
    plan.shots[0].scene_id = "missing_scene"
    plan.shots[0].previous_shot_id = "missing_shot"
    plan.shots[0].state_before.prop_states = {"missing_prop": {"prop_id": "missing_prop"}}
    report = RoleOutputValidator().validate(plan, p, scene=p.scenes[0])
    codes = {i.code.value for i in report.issues}
    assert {"unknown_entity_id", "broken_reference", "duration_budget_error"} <= codes
    assert any("missing_prop" in i.message for i in report.issues)


def test_partial_cinematography_commit_survives_restart(tmp_path):
    class MultiSceneProvider(FakeReasoningProvider):
        async def submit(self, request):
            name = request.generation_request.parameters["response_format"]["json_schema"]["name"]
            context = json.loads(request.generation_request.prompt_package.positive_prompt)
            if name == "ShotPlanDraft" and context["scene"]["scene_id"] == "scene_b" and self.fail_schema:
                raise ConnectionError("interrupted second scene")
            result = await super().submit(request)
            if name in {"Screenplay", "ScenePlan"}:
                content = json.loads(result.metadata["content"])
                content["scenes"].append({**content["scenes"][0], "scene_id": "scene_b"})
                result.metadata["content"] = json.dumps(content)
            return result

    async def scenario():
        first = ReasoningMovieProduction(tmp_path, MultiSceneProvider(fail_schema="second_scene"))
        with pytest.raises(ConnectionError):
            await first.run(brief())
        assert first.role_results["cinematographer:scene_a"].committed
        provider = MultiSceneProvider()
        second = ReasoningMovieProduction(tmp_path, provider)
        result = await second.run(resume=True)
        assert result.completed and len(result.project.shots) == 2
        assert provider.calls == ["ShotPlanDraft"]
    asyncio.run(scenario())


def test_exhausted_budget_is_persisted_and_not_reset_by_resume(tmp_path):
    class InvalidProvider(FakeReasoningProvider):
        async def submit(self, request):
            result = await super().submit(request)
            result.metadata["content"] = "{}"
            return result
    async def scenario():
        provider = InvalidProvider()
        first = ReasoningMovieProduction(tmp_path, provider)
        with pytest.raises(Exception, match="ROLE_OUTPUT_INVALID"):
            await first.run(brief())
        assert len(provider.calls) == 3
        assert first.job_manager.all()[0].retry_count == 2
        provider2 = InvalidProvider()
        second = ReasoningMovieProduction(tmp_path, provider2)
        with pytest.raises(Exception, match="ROLE_OUTPUT_INVALID"):
            await second.run(resume=True)
        assert provider2.calls == []
        assert second.job_manager.all()[0].retry_count == 2
        second.revise_failed_role("creative_producer")
        assert len(second.role_revision_history) == 1
        with pytest.raises(ValueError):
            second.revise_failed_role("creative_producer")
    asyncio.run(scenario())


def test_core_shot_state_can_carry_ambient_canonical_entities(tmp_path):
    from movie_agent.domain import Prop, PropState
    production = ReasoningMovieProduction(tmp_path, FakeReasoningProvider())
    result = asyncio.run(production.run(brief(), stop_after_node="shot_planning"))
    p, scene = result.project, result.project.scenes[0]
    p.props.append(Prop(prop_id="ambient_console", name="Console"))
    ambient = PropState(prop_id="ambient_console", condition="intact")
    scene.initial_state.prop_states["ambient_console"] = ambient.model_copy(deep=True)
    scene.expected_final_state.prop_states["ambient_console"] = ambient.model_copy(deep=True)
    plan = ShotPlan.model_validate(production.role_results["cinematographer:scene_a"].output)
    plan.continuity_chains[0].initial_state = scene.initial_state.model_copy(deep=True)
    # Ambient state is carried by the chain; no guessed values or post-LLM patching is needed.
    assert RoleOutputValidator().validate(plan, p, scene=scene).valid
    plan.shots[0].state_before.prop_states["ambient_console"] = ambient.model_copy(deep=True)
    assert RoleOutputValidator().validate(plan, p, scene=scene).valid
    scene.expected_final_state.prop_states["ambient_console"].condition = "damaged"
    # Only unchanged ambient state may be inherited; changing it must not silently pass.
    assert not RoleOutputValidator().validate(plan, p, scene=scene).valid
