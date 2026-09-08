"""Browser fixture using the real application with deterministic media providers."""

from tempfile import TemporaryDirectory
from pathlib import Path

from movie_agent.api.app import create_app
from movie_agent.application.repository import ProductionStatus
from movie_agent.services.reasoning_production import ReasoningMovieProduction
from tests.test_p4b_human_api import service_at
from tests.test_p4b_repair_loop import ControlledVision, FIXTURE


class FirstShotCritic(ControlledVision):
    target = None

    async def inspect(self, request):
        self.target = self.target or request.shot_id
        # Only the first shot triggers a review, then its repaired v2 passes.
        return await super().inspect(request.model_copy(update={
            "shot_id": "shot_001" if request.shot_id == self.target else "shot_002"}))


directory = TemporaryDirectory(prefix="p4b-browser-")
service = service_at(Path(directory.name), FirstShotCritic())
app = create_app(service)


@app.post('/test/media-project')
async def media_project():
    record = service.create(ReasoningMovieProduction.load_brief(FIXTURE))
    pid = record.project.project_id
    await service.engine(pid).run(resume=True)
    record = service.repository.get(pid)
    record.status = ProductionStatus.WAITING_HUMAN
    service.repository.save(record)
    return {'project_id': pid}
