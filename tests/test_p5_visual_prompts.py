from types import SimpleNamespace as NS
import pytest
from movie_agent.artifacts import LocalArtifactStore
from movie_agent.domain import ProviderResult
from movie_agent.quality.visual_prompts import english_visual_prompt


@pytest.mark.asyncio
async def test_visual_translation_cache_keeps_canonical_input_and_rejects_nonenglish(tmp_path):
    calls=[]
    async def submit(request):
        calls.append(request)
        return ProviderResult(provider_request_id=request.provider_request_id,success=True,metadata={
            'finish_reason':'stop','content':'{"instruction":"Lynne in a pale blue suspension suit, cold reflected light."}'})
    engine=NS(artifact_store=LocalArtifactStore(tmp_path),llm_provider=NS(provider_id='test',submit=submit),
        media_runtime=NS(runtime_coordinator=None),_durable_media_checkpoint=lambda:None)
    project=NS(project_id='p');source='林恩穿淡蓝色悬浮服，冷色反射光。'
    result=await english_visual_prompt(engine,project,source)
    assert result.startswith('Lynne') and calls[0].generation_request.prompt_package.positive_prompt==source
    assert await english_visual_prompt(engine,project,source)==result and len(calls)==1
    assert await english_visual_prompt(engine,project,'English already')=='English already' and len(calls)==1
    async def invalid(request):
        return ProviderResult(provider_request_id=request.provider_request_id,success=True,metadata={
            'finish_reason':'stop','content':'{"instruction":"仍然是中文"}'})
    engine.llm_provider.submit=invalid
    with pytest.raises(ValueError,match='no image/video dispatched'):
        await english_visual_prompt(engine,project,'另一个画面')
    reservations=[a for a in engine.artifact_store.list_all() if a.metadata.get('purpose')=='visual_prompt_dispatch']
    assert len(reservations)==4  # one successful source plus three rejected revisions
    with pytest.raises(ValueError,match='budget exhausted'):
        await english_visual_prompt(engine,project,'另一个画面')
    assert len([a for a in engine.artifact_store.list_all() if a.metadata.get('purpose')=='visual_prompt_dispatch'])==4
