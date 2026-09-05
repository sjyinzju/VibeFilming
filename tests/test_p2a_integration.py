"""Explicit opt-in integration tests; ordinary pytest never requires the endpoint."""

import asyncio
import os
import pytest
from pydantic import Field
from movie_agent.domain import ContractModel
from movie_agent.config import LLMConfig
from movie_agent.providers.openai_compatible import OpenAICompatibleLLMProvider
from movie_agent.orchestration.runtime.runner import StructuredOutputAdapter
from tests.test_p2a_provider import request_for_provider


class EndpointProbe(ContractModel):
    """Small schema probe exercising constrained decoding without production inference."""
    acknowledgement: str = Field(min_length=1, max_length=40)


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("MOVIE_AGENT_RUN_INTEGRATION") != "1", reason="opt in with MOVIE_AGENT_RUN_INTEGRATION=1")
def test_real_endpoint_health_and_structured_completion():
    async def scenario():
        provider = OpenAICompatibleLLMProvider(LLMConfig.from_env())
        try:
            assert await provider.health(), "Configured endpoint or model is unavailable"
            request = request_for_provider()
            request.generation_request.parameters["response_format"] = StructuredOutputAdapter().response_format(EndpointProbe)
            request.generation_request.parameters["max_tokens"] = 128
            result = await provider.submit(request)
            assert result.success, result.error_type
            validated = EndpointProbe.model_validate_json(result.metadata["content"])
            assert validated.acknowledgement
            assert result.metadata["usage"]
        finally:
            await provider.aclose()
    asyncio.run(scenario())
