"""Stop only the confirmed failed FLUX loader; no generation was admitted."""
import asyncio,json
from pathlib import Path
import httpx
from movie_agent.providers.registry import MediaProviderSettings
from movie_agent.model_services.resources import ResourceRuntimeSettings
from movie_agent.model_services.spark import SparkDockerServiceController

async def main():
    settings=MediaProviderSettings.from_env()
    async with httpx.AsyncClient(timeout=10,trust_env=False) as client:
        response=await client.get(settings.flux_endpoint+'/health')
    evidence=response.json()
    assert response.status_code==503 and evidence['state']=='failed' and evidence['load_error_code']=='RESOURCE_EXHAUSTED'
    controller=SparkDockerServiceController(ResourceRuntimeSettings.from_env())
    stopped=await controller.action('flux','stop')
    Path('workspace/p5-hero-film/flux-startup-failure.json').write_text(json.dumps({
        'health':evidence,'stopped':stopped,'inference_dispatched':False,
        'reason':'Failed loader is terminal; preserve failure and retry only after cold-start admission fix.'},indent=2))
    print('Confirmed failed FLUX loader stopped through allow-listed controller.')

if __name__=='__main__':asyncio.run(main())
