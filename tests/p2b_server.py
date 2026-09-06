"""Explicit offline E2E composition: real API/Core/SSE, fake reasoning, Mock media."""
import asyncio
import os
from pathlib import Path
from movie_agent.api.app import create_app as api_app
from tests.p2a_fakes import FakeReasoningProvider
from tests.test_p2a_api import service_at


class StudioTestProvider(FakeReasoningProvider):
    async def submit(self, request):
        await asyncio.sleep(0.4)
        return await super().submit(request)


def create_app():
    root = Path(os.environ.get('MOVIE_AGENT_E2E_WORKSPACE', 'workspace/p2b-e2e'))
    return api_app(service_at(root, StudioTestProvider()))
