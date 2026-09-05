"""Role-specific budgets, split timeouts, and uncertain-completion recovery."""

import asyncio
import json
import httpx
import pytest

from movie_agent.config import LLMConfig
from movie_agent.domain import ShotPlan, Scene, PropState, EventType, ProviderErrorType
from movie_agent.execution import LocalEventBus
from movie_agent.orchestration.runtime.budgets import scene_shot_budget, shot_output_budget
from movie_agent.orchestration.runtime.context import ContextBuilder
from movie_agent.orchestration.runtime.contracts import RoleId, RoleInvocation
from movie_agent.orchestration.runtime.registry import RoleRegistry
from movie_agent.orchestration.runtime.runner import RoleRunner, StructuredOutputAdapter
from movie_agent.providers.base import ProviderFailure
from movie_agent.providers.openai_compatible import OpenAICompatibleLLMProvider
from tests.test_p2a_provider import request_for_provider, project, direction
from tests.p2a_fakes import FakeReasoningProvider
from tests.test_p2a_runtime import brief
from movie_agent.services.reasoning_production import ReasoningMovieProduction


@pytest.mark.parametrize("default_read", [180, 222])
def test_default_role_inherits_read_timeout_and_cinematographer_overrides(default_read):
    provider = OpenAICompatibleLLMProvider(LLMConfig(base_url="http://test/v1", model="x", timeout=default_read))
    registry = RoleRegistry()
    for role in RoleId:
        definition = registry.get(role)
        policy = definition.inference_policy.resolve(provider.inference_defaults(),
            max_tokens=definition.output_policy.max_tokens)
        assert policy.read_timeout == (360 if role == RoleId.CINEMATOGRAPHER else default_read)
        assert (policy.connect_timeout, policy.write_timeout, policy.pool_timeout) == (10, 30, 10)
        assert policy.structured_output and not policy.thinking
    asyncio.run(provider.aclose())


def test_transport_split_timeout_and_output_ceiling():
    async def scenario():
        async def handler(request):
            assert request.extensions["timeout"] == {"connect": 10, "read": 360, "write": 30, "pool": 10}
            body = json.loads(request.content)
            assert body["max_tokens"] == 9000
            assert body["chat_template_kwargs"] == {"enable_thinking": False}
            assert body["response_format"]["type"] == "json_schema"
            return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleLLMProvider(LLMConfig(base_url="http://test/v1", model="x",
                max_tokens=9000, enable_thinking=True), client=client)
            request = request_for_provider()
            request.generation_request.parameters["inference_policy"] = RoleRegistry().get(
                RoleId.CINEMATOGRAPHER).inference_policy.model_dump(mode="json")
            assert (await provider.submit(request)).success
    asyncio.run(scenario())


def test_scene_budget_and_filtered_context_reserve_unfinished_scenes(tmp_path):
    production = ReasoningMovieProduction(tmp_path, FakeReasoningProvider())
    result = asyncio.run(production.run(brief(), stop_after_node="shot_planning"))
    p = result.project
    p.brief.target_duration = 18
    p.brief.max_shots = 6
    p.scenes.extend([p.scenes[0].model_copy(update={"scene_id": "scene_b", "shot_ids": []}),
                     p.scenes[0].model_copy(update={"scene_id": "scene_c", "shot_ids": []})])
    p.shots[0].duration_seconds = 6
    allocation = scene_shot_budget(p, p.scenes[1])
    assert allocation.remaining_scene_count == 2
    assert allocation.remaining_duration == 12
    assert allocation.remaining_shot_budget == 5
    assert allocation.scene_duration_budget == 6
    assert allocation.recommended_min_shots == 1 and allocation.max_shots == 2
    assert shot_output_budget(1) == 7000
    assert shot_output_budget(2) == shot_output_budget(6) == 12000
    p.story_bible.acts = ["OTHER SCENE SECRET HISTORY"]
    p.story_bible.character_arcs = {"unused": "OTHER SCENE SECRET HISTORY"}
    p.scenes[2].purpose = "OTHER SCENE SECRET HISTORY"
    context = ContextBuilder().build(RoleRegistry().get(RoleId.CINEMATOGRAPHER), p, scene=p.scenes[1])
    assert "OTHER SCENE SECRET HISTORY" not in context.model_dump_json()
    assert "screenplay" not in context.payload and "continuity" not in context.payload
    assert "initial_state" not in context.payload["scene"]
    assert context.payload["canonical_chain_context"]["initial_state"] is not None
    assert context.payload["shot_budget"]["max_shots"] == 2
    assert set(context.payload["story_bible"]) == {"immutable_facts", "world_facts", "themes"}
    p.brief.max_shots = 2
    with pytest.raises(ValueError, match="Insufficient"):
        scene_shot_budget(p, p.scenes[1])


def test_read_timeout_requires_explicit_resume_without_consuming_repairs():
    async def scenario():
        calls = []
        saved = []
        async def handler(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ReadTimeout("private remote body", request=request)
            return httpx.Response(200, json={"choices": [{"message": {"content": direction().model_dump_json()}}],
                "usage": {"prompt_tokens": 80, "completion_tokens": 120}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleLLMProvider(LLMConfig(base_url="http://test/v1", model="x"), client=client)
            bus = LocalEventBus()
            runner = RoleRunner(provider, bus, "trace")
            p = project()
            invocation = RoleInvocation(role_id=RoleId.CREATIVE_PRODUCER, project_id=p.project_id,
                                        node_id="creative_expansion")
            with pytest.raises(ProviderFailure) as failure:
                await runner.run(invocation, p, on_attempt=lambda r: saved.append(r.model_copy(deep=True)))
            assert failure.value.error_type == ProviderErrorType.REMOTE_COMPLETION_UNCERTAIN
            assert not failure.value.retryable and len(calls) == 1
            previous = saved[-1]
            assert len(previous.attempts) == 0 and len(previous.inference_records) == 1
            assert previous.inference_records[0].validation_result == "not_run"
            assert previous.failure_code == "remote_completion_uncertain"
            assert "private remote body" not in previous.model_dump_json()
            assert not bus.events(EventType.ROLE_OUTPUT_VALIDATION_FAILED)
            result = await runner.run(invocation, p, previous=previous)
            assert len(calls) == 2 and len(result.attempts) == 1
            assert len(result.inference_records) == 2
            metrics = result.inference_records[-1]
            assert metrics.prompt_tokens == 80 and metrics.completion_tokens == 120
            assert metrics.ended_at >= metrics.started_at
            assert metrics.validation_result == "passed" and metrics.provider_outcome == "success"
            assert metrics.context_chars > 0 and metrics.input_token_estimate > 0 and metrics.schema_chars > 0
    asyncio.run(scenario())


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ConnectTimeout])
def test_connect_failure_distinguished_from_remote_uncertainty(error):
    async def scenario():
        def handler(request):
            raise error("offline", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleLLMProvider(LLMConfig(base_url="http://test/v1", model="x"), client=client)
            result = await provider.submit(request_for_provider())
            assert result.retryable and result.metadata["provider_outcome"] == "connect_failure"
            assert result.error_type in {ProviderErrorType.UNAVAILABLE, ProviderErrorType.TIMEOUT}
    asyncio.run(scenario())


def test_shot_schema_bounds_are_real_contract_refinements_without_domain_mutation():
    original = ShotPlan.model_json_schema()
    bounded = StructuredOutputAdapter().response_format(ShotPlan, max_scene_shots=2)["json_schema"]["schema"]
    assert bounded["properties"]["shots"]["maxItems"] == 2
    assert bounded["properties"]["continuity_chains"]["maxItems"] == 2
    chain_shots = bounded["$defs"]["ContinuityChain"]["properties"]["shot_ids"]
    assert chain_shots["minItems"] == 1 and chain_shots["maxItems"] == 2
    assert bounded["properties"]["shots"]["items"] == original["properties"]["shots"]["items"]
    assert ShotPlan.model_json_schema() == original


def test_structured_output_cannot_be_disabled():
    async def scenario():
        def handler(request):
            pytest.fail("Invalid schema policy must fail before network submission")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleLLMProvider(LLMConfig(base_url="http://test/v1", model="x"), client=client)
            request = request_for_provider()
            request.generation_request.parameters["inference_policy"] = {"structured_output": False}
            result = await provider.submit(request)
            assert result.error_type == ProviderErrorType.INVALID_REQUEST and not result.success
    asyncio.run(scenario())


def test_uncertain_result_is_cached_without_explicit_retry_flag():
    async def scenario():
        calls = []
        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("still computing", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleLLMProvider(LLMConfig(base_url="http://test/v1", model="x"), client=client)
            request = request_for_provider()
            first = await provider.submit(request)
            again = await provider.submit(request)
            assert again == first and len(calls) == 1
            assert not again.retryable
    asyncio.run(scenario())


def test_shot_boundary_decoding_scope_differs_from_exact_chain_canon():
    scene = Scene(scene_id="scene", title="Signal", purpose="Truth", location_id="room",
        time_description="night", character_ids=["person"], prop_ids=["lever"])
    scene.initial_state.prop_states["ambient_console"] = PropState(prop_id="ambient_console")
    original = ShotPlan.model_json_schema()
    schema = StructuredOutputAdapter().response_format(ShotPlan, max_scene_shots=2, scene=scene)["json_schema"]["schema"]
    boundary = schema["$defs"]["ShotBoundaryState"]
    for field, allowed in (("character_states", {"person"}), ("prop_states", {"lever"}), ("location_states", {"room"})):
        assert set(boundary["properties"][field]["properties"]) == allowed
        assert boundary["properties"][field]["additionalProperties"] is False
    for field in ("state_before", "expected_state_after"):
        assert schema["$defs"]["Shot"]["properties"][field]["$ref"] == "#/$defs/ShotBoundaryState"
    chain_initial = schema["$defs"]["ContinuityChain"]["properties"]["initial_state"]
    assert chain_initial["$ref"] == "#/$defs/ContinuityState"
    assert chain_initial["const"] == scene.initial_state.model_dump(mode="json")
    assert "ambient_console" in chain_initial["const"]["prop_states"]
    assert ShotPlan.model_json_schema() == original
