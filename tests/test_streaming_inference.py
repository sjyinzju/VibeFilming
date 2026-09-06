import asyncio
import json
import os

import httpx
import pytest
from movie_agent.config import LLMConfig
from movie_agent.providers.openai_compatible import OpenAICompatibleLLMProvider
from movie_agent.domain import ProviderErrorType, CreativeDirection
from movie_agent.execution import LocalEventBus
from movie_agent.orchestration.runtime.runner import RoleRunner
from movie_agent.orchestration.runtime.registry import RoleRegistry
from movie_agent.orchestration.runtime.contracts import RoleId, RoleInvocation
from tests.test_p2a_provider import request_for_provider, project, direction


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks, delay=0):
        self.chunks, self.delay = chunks, delay
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            await asyncio.sleep(self.delay)
            yield chunk

    async def aclose(self):
        self.closed = True


def frame(content='', finish=None):
    return ('data: ' + json.dumps({'id': 'remote', 'choices': [{'index': 0,
        'delta': {'content': content, 'reasoning_content': 'DO NOT PERSIST'}, 'finish_reason': finish}]}) + '\n\n').encode()


def test_stream_accumulates_fragmented_json_and_usage_without_total_read_deadline():
    async def scenario():
        encoded = [frame(part) for part in ['{', '"ok"', ':', 'true', '}']]
        encoded += [frame(finish='stop'), b'data: {"choices": [], "usage": {"completion_tokens": 8}}\n\n', b'data: [DONE]\n\n']
        stream = Chunks([part for chunk in encoded for part in (chunk[:9], chunk[9:])], delay=.01)
        def handler(request):
            body = json.loads(request.content)
            assert body['stream'] and body['stream_options']['include_usage']
            assert body['response_format']['type'] == 'json_schema'
            return httpx.Response(200, stream=stream)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleLLMProvider(LLMConfig(base_url='http://test/v1', model='x'), client=client)
            request = request_for_provider()
            request.generation_request.parameters['inference_policy'] = {
                'stream': True, 'read_timeout': .05, 'inactivity_timeout': .1, 'total_timeout': 2}
            result = await provider.submit(request)
            assert result.success and result.latency_seconds > .05
            assert json.loads(result.metadata['content']) == {'ok': True}
            assert result.metadata['usage']['completion_tokens'] == 8
            assert 'DO NOT PERSIST' not in result.model_dump_json()
            assert stream.closed
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['disconnect', 'missing_finish', 'malformed', 'inactivity', 'total'])
def test_incomplete_stream_never_returns_partial_content_or_retries(mode):
    async def scenario():
        chunks = [frame('{')]
        if mode == 'missing_finish': chunks += [b'data: [DONE]\n\n']
        if mode == 'malformed': chunks += [b'data: invalid\n\n']
        if mode in {'inactivity', 'total'}: chunks *= 20
        stream = Chunks(chunks, delay=.02 if mode in {'inactivity', 'total'} else 0)
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, stream=stream)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleLLMProvider(LLMConfig(base_url='http://test/v1', model='x'), client=client)
            request = request_for_provider()
            request.generation_request.parameters['inference_policy'] = {'stream': True,
                'inactivity_timeout': .01 if mode == 'inactivity' else 1,
                'total_timeout': .05 if mode == 'total' else 2}
            result = await provider.submit(request)
            assert result.error_type == ProviderErrorType.REMOTE_COMPLETION_UNCERTAIN
            assert not result.retryable and 'content' not in result.metadata
            assert await provider.submit(request) == result and len(calls) == 1
            if mode == 'total': assert result.metadata['failure_phase'] == 'total_budget'
            if mode == 'inactivity': assert result.metadata['failure_phase'] == 'read'
            assert stream.closed
    asyncio.run(scenario())


def test_cancel_stream_does_not_return_partial_json():
    async def scenario():
        stream = Chunks([frame('{')] * 100, delay=.01)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=stream))) as client:
            provider = OpenAICompatibleLLMProvider(LLMConfig(base_url='http://test/v1', model='x'), client=client)
            request = request_for_provider()
            request.generation_request.parameters['inference_policy'] = {'stream': True}
            task = asyncio.create_task(provider.submit(request))
            await asyncio.sleep(.03)
            assert await provider.cancel(request.provider_request_id)
            with pytest.raises(asyncio.CancelledError):
                await task
            saved = await provider.status(request.provider_request_id)
            assert saved.error_type == ProviderErrorType.CANCELLED
            assert 'content' not in saved.metadata and stream.closed
    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get('MOVIE_AGENT_RUN_STREAMING_INTEGRATION') != '1',
                    reason='opt in with MOVIE_AGENT_RUN_STREAMING_INTEGRATION=1')
def test_real_streaming_role_keeps_schema_and_validation():
    async def scenario():
        provider = OpenAICompatibleLLMProvider(LLMConfig.from_env())
        try:
            registry = RoleRegistry()
            policy = registry.get(RoleId.CREATIVE_PRODUCER).inference_policy
            policy.stream, policy.total_timeout = True, 180
            p = project()
            result = await RoleRunner(provider, LocalEventBus(), 'stream-test', registry).run(
                RoleInvocation(role_id=RoleId.CREATIVE_PRODUCER, project_id=p.project_id,
                               node_id='stream-probe'), p)
            output = CreativeDirection.model_validate(result.output)
            assert output.premise_expansion and result.attempts[-1].validation.valid
            assert result.inference_records[-1].completion_tokens > 0
            assert result.inference_records[-1].finish_reason == 'stop'
            assert p.creative_direction is None
        finally:
            await provider.aclose()
    asyncio.run(scenario())
