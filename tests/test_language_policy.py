import asyncio
import json
import os
import re

import pytest
from movie_agent.domain import Project, CreativeDirection, ProjectBrief
from movie_agent.orchestration.runtime.language import LanguagePolicy
from movie_agent.orchestration.runtime.runner import RoleRunner
from movie_agent.orchestration.runtime.contracts import RoleId, RoleInvocation
from movie_agent.services.reasoning_production import ReasoningMovieProduction
from movie_agent.execution.events import LocalEventBus
from tests.p2a_fakes import FakeReasoningProvider
from tests.test_p2a_runtime import brief


@pytest.mark.parametrize('language', ['zh-CN', 'en', ''])
def test_every_role_and_repair_share_language_policy_without_schema_translation(tmp_path, language):
    class Capturing(FakeReasoningProvider):
        def __init__(self):
            super().__init__()
            self.requests = []

        async def submit(self, request):
            self.requests.append(request.model_copy(deep=True))
            response = await super().submit(request)
            if len(self.requests) == 1:
                # Cause one structured repair, while preserving the same system policy.
                response.metadata['content'] = '{}'
            return response

    provider = Capturing()
    project_brief = brief().model_copy(update={'output_language': language})
    production = ReasoningMovieProduction(tmp_path, provider)
    result = asyncio.run(production.run(project_brief))
    assert result.completed
    assert len(provider.requests) == 7
    expected = LanguagePolicy.instruction(language)
    for request in provider.requests:
        params = request.generation_request.parameters
        assert expected in params['system_prompt']
        assert 'JSON keys' in params['system_prompt']
        assert 'world_rules' in params['system_prompt']
        schema = params['response_format']['json_schema']
        assert schema['strict'] is True
        assert 'schema_version' in schema['schema']['properties']
    first, repair = provider.requests[:2]
    assert first.generation_request.parameters['system_prompt'] == repair.generation_request.parameters['system_prompt']
    assert first.generation_request.parameters['response_format'] == repair.generation_request.parameters['response_format']
    assert 'original_task' in json.loads(repair.generation_request.prompt_package.positive_prompt)
    cinematography = provider.requests[-1].generation_request.parameters['response_format']
    assert 'wide' in json.dumps(cinematography)
    assert result.project.creative_direction.preserved_user_constraints == project_brief.user_constraints + project_brief.must_preserve
    assert result.project.brief.output_language == language  # Policy does not normalize canonical state.


def test_legacy_defaults_and_language_aliases():
    assert ProjectBrief(title='Legacy', logline='Legacy', story_description='Legacy', target_duration=12).output_language == 'en'
    assert LanguagePolicy.instruction(None) == LanguagePolicy.instruction('en')
    assert LanguagePolicy.instruction('zh_CN') == LanguagePolicy.instruction('zh-CN')
    assert 'fr' in LanguagePolicy.instruction('fr')


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get('MOVIE_AGENT_RUN_LANGUAGE_INTEGRATION') != '1',
                    reason='opt in with MOVIE_AGENT_RUN_LANGUAGE_INTEGRATION=1')
def test_real_creative_producer_simplified_chinese():
    from movie_agent.config import LLMConfig
    from movie_agent.providers.openai_compatible import OpenAICompatibleLLMProvider

    async def scenario():
        provider = OpenAICompatibleLLMProvider(LLMConfig.from_env())
        try:
            project = Project(brief=ProjectBrief(title='回声', logline='一个人决定回应信号',
                story_description='一位档案员在安静的房间听到来自明天的信号，决定回应。简洁的十二秒短片。',
                target_duration=12, output_language='zh-CN', must_preserve=['Keep the ending open.']))
            result = await RoleRunner(provider, LocalEventBus(), 'language-policy-test').run(RoleInvocation(role_id=RoleId.CREATIVE_PRODUCER,
                project_id=project.project_id, node_id='language-policy-probe'), project)
            output = CreativeDirection.model_validate(result.output)
            for text in (output.premise_expansion, output.emotional_arc, output.tone):
                assert re.search(r'[\u4e00-\u9fff]', text), 'Target field lacks Chinese creative content'
            assert output.preserved_user_constraints == ['Keep the ending open.']
            assert result.attempts[-1].validation.valid
            assert 'premise_expansion' in result.output
        finally:
            await provider.aclose()
    asyncio.run(scenario())
