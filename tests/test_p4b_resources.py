"""Fourth-service integration with P4A's existing scheduler and lease rules."""

import asyncio
import json
import pytest

from movie_agent.domain import GenerationJob, ResourceClass, ProviderResult, ProviderErrorType
from movie_agent.media import MediaModality
from movie_agent.model_services.resources import GiB, ResourceRuntimeSettings, LeaseStatus
from movie_agent.model_services.spark import SparkDockerServiceController, SparkDockerModelService, CONTAINERS
from movie_agent.model_services.coordinator import RuntimeCoordinator, ResourceAdmissionWait
from movie_agent.providers.base import ProviderFailure
from movie_agent.providers.registry import MediaProviderSettings
from movie_agent.model_services.wiring import build_spark_runtime
from tests.test_resource_runtime import Service, environment, job, success
from tests.test_p4b_vision import stores, request
from movie_agent.media.runtime import MediaRuntime
from movie_agent.providers.media import MockVisionProvider
from movie_agent.providers.registry import ProviderRegistry
from movie_agent.execution import JobManager,LocalEventBus


def with_vlm(tmp_path=None):
    runtime,memory=environment(tmp_path)
    vlm=Service('vlm',memory,peak=90,resident=75)
    vlm.descriptor.modality=MediaModality.VISION
    vlm.descriptor.runtime_profile.provider_id='qwen3_vl'
    vlm.descriptor.runtime_profile.requires_exclusive_runtime=True
    runtime.manager.register(vlm)
    return runtime,memory,vlm


def test_vlm_allowlist_and_composition(tmp_path):
    from movie_agent.config import LLMConfig
    settings=ResourceRuntimeSettings(enabled=False,state_path=str(tmp_path/'state.json'))
    runtime=build_spark_runtime(LLMConfig(base_url='http://127.0.0.1:8000/v1',model='reasoning'),MediaProviderSettings(),settings=settings)
    descriptor=runtime.manager.get('vlm').descriptor
    assert CONTAINERS['vlm']=='movie-agent-vlm'
    assert descriptor.runtime_profile.provider_id=='qwen3_vl'
    assert descriptor.runtime_profile.model_profile_id=='Qwen3-VL-30B-A3B-Thinking'
    assert descriptor.metadata['served_model']=='movie-agent-vision'
    assert descriptor.capabilities.requires_resource_lease
    with pytest.raises(ValueError):SparkDockerServiceController(settings)._container('arbitrary-container')


@pytest.mark.asyncio
async def test_vlm_lease_idle_eviction_and_warm_affinity():
    runtime,memory,vlm=with_vlm()
    for sid in ['flux','h3']:
        lease=await runtime.acquire(job(sid));await runtime.release(lease)
    vision=job('qwen3_vl',job_id='vision',key='vision')
    async def inspect(active):
        assert runtime.active_leases('vlm')[0].status==LeaseStatus.EXECUTING
        assert vlm.descriptor.status=='busy'
        assert not await runtime._evict(vlm,'test')
        return await success(active)
    assert (await runtime.execute(vision,inspect)).success
    assert not runtime.active_leases()
    assert runtime.manager.get('h3').stops>=1
    assert vlm.starts==1
    next_job=job('qwen3_vl',job_id='vision-next',key='vision-next')
    runtime.set_ready_jobs([next_job]);await runtime.maintain()
    assert vlm.stops==0
    assert (await runtime.execute(next_job,success)).success
    assert vlm.starts==1
    runtime.set_ready_jobs([])
    runtime.settings.warm_idle_ttl = 0
    await runtime.maintain()
    assert vlm.stops == 1 and vlm.descriptor.status == 'stopped'


@pytest.mark.asyncio
async def test_vlm_busy_or_unknown_activity_never_evicted():
    runtime,_,vlm=with_vlm()
    lease=await runtime.acquire(job('qwen3_vl'))
    await runtime.release(lease)
    vlm.busy_external=True
    with pytest.raises(ResourceAdmissionWait):await runtime.acquire(job('h3'))
    assert vlm.stops==0


@pytest.mark.asyncio
async def test_vlm_uncertain_request_quarantines_lease_across_restart(tmp_path):
    runtime,memory,vlm=with_vlm(tmp_path)
    async def disconnected(active):
        return ProviderResult(provider_request_id='vision',success=False,error_type=ProviderErrorType.REMOTE_COMPLETION_UNCERTAIN)
    assert not (await runtime.execute(job('qwen3_vl'),disconnected)).success
    assert runtime.active_leases('vlm')[0].status==LeaseStatus.UNCERTAIN
    restored=RuntimeCoordinator(runtime.manager,memory,state_path=tmp_path/'state.json')
    with pytest.raises(ResourceAdmissionWait):await restored.acquire(job('qwen3_vl',job_id='again',key='again'))
    assert not await restored.reconcile(restored.active_leases()[0].lease_id)


def test_vlm_cannot_execute_without_resource_lease(tmp_path):
    async def scenario():
        artifacts,binaries=stores(tmp_path)
        class Heavy(MockVisionProvider):
            provider_id='qwen3_vl'
            calls=0
            async def capabilities(self):return (await super().capabilities()).model_copy(update={'requires_resource_lease':True})
            async def inspect(self,req):self.calls+=1;return await super().inspect(req)
        provider=Heavy();registry=ProviderRegistry();registry.register(provider)
        events=LocalEventBus();media=MediaRuntime(artifacts,binaries,registry,events,'trace')
        media.bind_jobs(JobManager(events,'trace'))
        req=request().model_copy(update={'target_sha256':None})
        active=GenerationJob(job_id=req.job_id,project_id='project',task='vision',idempotency_key=req.job_id,resource_class=ResourceClass.LIGHT)
        with pytest.raises(ProviderFailure):await media.inspect(active,req)
        assert provider.calls==0
    asyncio.run(scenario())


@pytest.mark.asyncio
@pytest.mark.parametrize('recover', [True, False])
async def test_vlm_preparation_failure_retries_without_invalid_transition(recover):
    from movie_agent.execution import LocalJobExecutor
    from movie_agent.domain import JobStatus
    runtime, memory, vlm = with_vlm()
    original = memory.snapshot
    attempts = 0
    calls = 0
    async def interrupted_telemetry():
        nonlocal attempts
        attempts += 1
        if not recover or attempts == 1:
            raise ProviderFailure('telemetry unavailable', ProviderErrorType.UNAVAILABLE, retryable=True)
        return await original()
    memory.snapshot = interrupted_telemetry
    manager = JobManager(LocalEventBus(), 'trace')
    manager.runtime_coordinator = runtime
    manager.add(job('qwen3_vl', retry_budget=1))
    async def inspect(active):
        nonlocal calls
        calls += 1
        assert active.status == JobStatus.RUNNING
        assert runtime.active_leases('vlm')[0].status == LeaseStatus.EXECUTING
        return await success(active)
    result = await LocalJobExecutor(manager).execute('qwen3_vl', inspect)
    assert result.status == (JobStatus.SUCCEEDED if recover else JobStatus.FAILED)
    assert result.retry_count == 1
    assert calls == int(recover)
    assert not runtime.active_leases()


@pytest.mark.asyncio
async def test_missing_inference_telemetry_is_unknown_not_zero_peak():
    runtime, memory, _ = with_vlm()
    async def unavailable():
        raise TimeoutError('telemetry unavailable')
    async def inspect(active):
        memory.snapshot = unavailable
        return await success(active)
    result = await runtime.execute(job('qwen3_vl'), inspect)
    assert result.success and not runtime.active_leases()
    observation = runtime.observations[-1]
    assert observation.resource_after is None and observation.resource_peak is None
    assert observation.observed_peak_bytes is None
    assert runtime.manager.get('vlm').descriptor.runtime_profile.observed_peak_bytes is None
