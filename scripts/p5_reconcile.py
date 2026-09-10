"""Recover a completed-but-misclassified local critic validation through P4A only."""
import asyncio,json
from movie_agent.config import LLMConfig
from movie_agent.providers.registry import MediaProviderSettings
from movie_agent.model_services.wiring import build_spark_runtime

async def main():
    runtime=build_spark_runtime(LLMConfig.from_env(),MediaProviderSettings.from_env().model_copy(update={'kontext_enabled':True}))
    try:
        for lease in list(runtime.active_leases()):
            if lease.service_id!='qwen' or not lease.job_id.startswith('cinematic_'): continue
            service=runtime.manager.get('qwen')
            idle=await service.idle()
            print(json.dumps({'job':lease.job_id,'idle':idle}),flush=True)
            if not idle: raise RuntimeError('Qwen still has pending/running work; no stop or replay')
            await service.stop()
            assert await runtime.reconcile(lease.lease_id)
    finally:runtime.close()

if __name__=='__main__':asyncio.run(main())
