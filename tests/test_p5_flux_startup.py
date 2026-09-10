from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from movie_agent.media import ModelServiceStatus
from movie_agent.providers.base import ProviderFailure
from movie_agent.domain import ProviderErrorType
from movie_agent.model_services.spark import SparkDockerModelService
from movie_agent.model_services.resources import GiB
from tests.test_resource_runtime import Service, Memory, environment


@pytest.mark.asyncio
async def test_live_container_with_terminal_loader_oom_is_failed_idle_and_not_warming():
    controller=SimpleNamespace(action=AsyncMock(),inspect=AsyncMock(return_value={'Running':True}),
        is_controlled_stop=lambda *_:False)
    service=SparkDockerModelService(Service('flux',Memory()).descriptor,controller,
        health_path='/health',idle_path='/health',kind='flux')
    async def get(path,**kwargs):
        assert kwargs['allow_unready']
        return httpx.Response(503,json={'ready':False,'state':'failed','load_error_code':'RESOURCE_EXHAUSTED'})
    service._get=get
    assert await service.status()==ModelServiceStatus.FAILED
    assert await service.idle()
    with pytest.raises(ProviderFailure) as error:
        await service.start()
    assert error.value.error_type==ProviderErrorType.RESOURCE_EXHAUSTED_OOM
    assert controller.action.await_args_list[0].args==('flux','stop')


def test_cold_allocator_requires_free_pages_even_when_memavailable_includes_cache():
    runtime,_=environment()
    target=runtime.manager.get('flux').descriptor
    target.metadata['minimum_cold_start_free_bytes']=48*GiB
    from movie_agent.model_services.resources import ResourceSnapshot
    snapshot=ResourceSnapshot(total_unified_memory_bytes=128*GiB,available_unified_memory_bytes=60*GiB,
        diagnostics={'free_unified_memory_bytes':14*GiB})
    assert not runtime.can_admit('flux',snapshot)[0]
    snapshot.diagnostics['free_unified_memory_bytes']=60*GiB
    assert runtime.can_admit('flux',snapshot)[0]
    target.status=ModelServiceStatus.READY
    snapshot.diagnostics['free_unified_memory_bytes']=14*GiB
    assert runtime.can_admit('flux',snapshot)[0]
