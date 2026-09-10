"""Offline browser fixture: fake audio HTTP transport, actual local FFmpeg bytes."""
import asyncio
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from movie_agent.api.app import create_app
from movie_agent.application.service import ProductionService
from movie_agent.application.repository import ProjectRecord, ProductionStatus
from movie_agent.storage.projects import LocalProjectRepository
from tests.test_p4d_audio import audio_engine

root = Path(tempfile.mkdtemp(prefix='movie-p4d-browser-'))
def fixture():
    engine, calls = audio_engine(root / 'media', studio=True)
    asyncio.run(engine.run(resume=True))
    return engine, calls


with ThreadPoolExecutor(max_workers=1) as pool:
    engine, calls = pool.submit(fixture).result()
pid = engine.current_project.project_id
service = ProductionService(LocalProjectRepository(root), lambda _: engine)
service.engines[pid] = engine
service.repository.save(ProjectRecord(project=engine.current_project, status=ProductionStatus.COMPLETED))
app = create_app(service)


@app.get('/p4d/project')
async def project():
    return {'project_id': pid, 'offline_fixture': True, 'audio_requests': len(calls)}
