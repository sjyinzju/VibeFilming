"""Transport contracts tested without a remote model."""

import asyncio
import json
import httpx
import pytest
from movie_agent.config import LLMConfig
from movie_agent.domain import (CreativeDirection, Project, ProjectBrief, EventType, ProviderRequest,
    GenerationRequest, GenerationStrategy, PromptPackage, ProviderErrorType)
from movie_agent.providers.openai_compatible import CompletionOptions
from movie_agent.orchestration.runtime.runner import StructuredOutputAdapter
from movie_agent.execution import LocalEventBus
from movie_agent.providers.openai_compatible import OpenAICompatibleLLMProvider
from movie_agent.orchestration.runtime.runner import RoleRunner, RoleOutputInvalid
from movie_agent.orchestration.runtime.contracts import RoleInvocation, RoleId


def project():
    return Project(brief=ProjectBrief(title="Signal", logline="A signal stops", story_description="Isolation",
        target_duration=12, user_constraints=["One visible person"], must_preserve=["Truth wins"]))


def direction():
    return CreativeDirection(premise_expansion="A silent signal creates a choice", emotional_arc="Fear to courage",
        tone="intimate", preserved_user_constraints=["One visible person", "Truth wins"])


def test_config_env_precedence_and_secret(tmp_path):
    config = LLMConfig.from_env(tmp_path / "absent", environ={
        "MOVIE_AGENT_LLM_BASE_URL": "http://test/v1/", "MOVIE_AGENT_LLM_MODEL": "test-model",
        "MOVIE_AGENT_LLM_API_KEY": "SECRET", "MOVIE_AGENT_LLM_TIMEOUT": "12"})
    assert config.base_url == "http://test/v1" and config.timeout == 12
    assert "SECRET" not in repr(config) and "SECRET" not in config.model_dump_json()
    with pytest.raises(ValueError):
        LLMConfig(base_url="file:///tmp", model="x")


def test_creative_role_real_transport_shape_and_validation():
    async def scenario():
        bodies = []
        async def transport(request):
            if request.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": [{"id": "test-model"}]})
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "r", "choices": [{"message": {
                "content": direction().model_dump_json(), "reasoning": "DO NOT STORE"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            provider = OpenAICompatibleLLMProvider(LLMConfig(base_url="http://test/v1", model="test-model"), client=client)
            assert await provider.health()
            bus = LocalEventBus()
            p = project()
            result = await RoleRunner(provider, bus, "trace").run(RoleInvocation(role_id=RoleId.CREATIVE_PRODUCER,
                project_id=p.project_id, node_id="creative_expansion"), p)
            assert result.output == direction().model_dump(mode="json")
            schema = bodies[0]["response_format"]["json_schema"]["schema"]
            assert schema["properties"] == CreativeDirection.model_json_schema()["properties"]
            assert set(schema["required"]) == set(schema["properties"])
            assert bodies[0]["chat_template_kwargs"] == {"enable_thinking": False}
            assert "DO NOT STORE" not in result.model_dump_json()
            assert p.creative_direction is None  # Runner returns a candidate; application commits.
            assert bus.events(EventType.ROLE_OUTPUT_VALIDATED)
    asyncio.run(scenario())


def test_invalid_output_finite_repair():
    async def scenario():
        attempts = []
        async def transport(request):
            attempts.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            provider = OpenAICompatibleLLMProvider(LLMConfig(base_url="http://test/v1", model="x"), client=client)
            p = project()
            with pytest.raises(RoleOutputInvalid) as failure:
                await RoleRunner(provider, LocalEventBus(), "t").run(RoleInvocation(
                    role_id=RoleId.CREATIVE_PRODUCER, project_id=p.project_id, node_id="creative_expansion"), p)
            assert len(failure.value.result.attempts) == len(attempts) == 3
            assert "validation_errors" in attempts[1]["messages"][1]["content"]
    asyncio.run(scenario())


def request_for_provider():
    return ProviderRequest(provider_id="reasoning", generation_request=GenerationRequest(
        job_id="probe", task="structured_text", requested_output_type="structured_text",
        strategy=GenerationStrategy(strategy_type="structured_text", reason="test"),
        prompt_package=PromptPackage(compiler_id="test", compiler_version="1", positive_prompt="Say hello"),
        parameters=CompletionOptions(system_prompt="Test", response_format=StructuredOutputAdapter().response_format(
            CreativeDirection)).model_dump(mode="json")))


@pytest.mark.parametrize("status,kind,retry", [(400,"invalid_request",False), (401,"invalid_request",False),
    (404,"invalid_request",False), (429,"resource_exhausted",True), (500,"unavailable",True), (503,"unavailable",True)])
def test_http_error_normalization(status, kind, retry):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(status, text="remote SECRET reasoning content"))) as client:
            p = OpenAICompatibleLLMProvider(LLMConfig(base_url="http://test/v1", model="x"), client=client)
            result = await p.submit(request_for_provider())
            assert not result.success and result.error_type.value == kind and result.retryable == retry
            assert "SECRET" not in result.model_dump_json()
    asyncio.run(scenario())


def test_timeout_and_model_health_mismatch():
    async def scenario():
        def handler(request):
            if request.method == "GET":
                return httpx.Response(200, json={"data": [{"id": "wrong"}]})
            raise httpx.ReadTimeout("SECRET", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            p = OpenAICompatibleLLMProvider(LLMConfig(base_url="http://test/v1", model="x"), client=client)
            assert not await p.health()
            r = await p.submit(request_for_provider())
            assert r.error_type == ProviderErrorType.REMOTE_COMPLETION_UNCERTAIN and not r.retryable
            assert "SECRET" not in r.model_dump_json()
    asyncio.run(scenario())


def test_cancel_is_local_and_status_is_honest():
    async def scenario():
        entered = asyncio.Event()
        async def handler(request):
            entered.set()
            await asyncio.Event().wait()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            p = OpenAICompatibleLLMProvider(LLMConfig(base_url="http://test/v1", model="x"), client=client)
            request = request_for_provider()
            assert await p.status(request.provider_request_id) is None
            task = asyncio.create_task(p.submit(request))
            await entered.wait()
            assert not (await p.capabilities()).supports_cancellation
            assert await p.cancel(request.provider_request_id)
            with pytest.raises(asyncio.CancelledError):
                await task
            assert (await p.status(request.provider_request_id)).error_type == ProviderErrorType.CANCELLED
            assert not await p.cancel(request.provider_request_id)
    asyncio.run(scenario())


def test_explicit_resubmit_retries_transient_failure_but_reuses_success():
    async def scenario():
        calls = []
        async def handler(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ConnectError("offline", request=request)
            return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleLLMProvider(LLMConfig(base_url="http://test/v1", model="x"), client=client)
            request = request_for_provider()
            failed = await provider.submit(request)
            assert not failed.success and failed.retryable
            assert len(calls) == 1  # No automatic transport retries.
            assert (await provider.status(request.provider_request_id)).error_type == ProviderErrorType.UNAVAILABLE
            recovered = await provider.submit(request)
            assert recovered.success
            assert (await provider.submit(request)).success
            assert (await provider.status(request.provider_request_id)).success
            assert len(calls) == 2
    asyncio.run(scenario())
