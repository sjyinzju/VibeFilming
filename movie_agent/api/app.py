"""REST commands/snapshots and replayable server-sent events."""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from movie_agent.domain import ProjectBrief, Shot, HumanReviewRequest, GenerationJob, Artifact
from movie_agent.application.service import ProductionService, CommandConflict
from movie_agent.application.repository import ProjectRecord
from movie_agent.application.views import ProjectSnapshot, WorkflowSnapshot, CommandAccepted, ProviderView
from movie_agent.application.creative_inputs import CreateProjectInput
from movie_agent.application.studio import StudioSnapshot, studio_snapshot


class ReviewResolution(BaseModel):
    """Explicit human approval/rejection command."""
    model_config = ConfigDict(extra="forbid")
    approved: bool
    notes: str | None = None


def create_app(service: ProductionService | None = None) -> FastAPI:
    """Compose local adapters by default; tests and future storage inject a service."""
    owned_provider = None
    if service is None:
        from movie_agent.config import LLMConfig
        from movie_agent.providers.openai_compatible import OpenAICompatibleLLMProvider
        from movie_agent.storage.projects import LocalProjectRepository
        from movie_agent.execution.durable_events import DurableLocalEventBus
        from movie_agent.services.reasoning_production import ReasoningMovieProduction
        root = Path(os.environ.get("MOVIE_AGENT_WORKSPACE", "workspace/studio")).resolve()
        owned_provider = OpenAICompatibleLLMProvider(LLMConfig.from_env())
        def factory(project_id):
            workspace = root / project_id
            return ReasoningMovieProduction(workspace, owned_provider,
                event_bus=DurableLocalEventBus(workspace / "events"))
        service = ProductionService(LocalProjectRepository(root), factory)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await service.shutdown()
        if owned_provider:
            await owned_provider.aclose()

    app = FastAPI(title="Movie Agent Phase 2A", version="0.2.0", lifespan=lifespan)
    app.state.production_service = service

    @app.exception_handler(KeyError)
    async def not_found(request, error):
        return JSONResponse(status_code=404, content={"detail": "Resource not found"})

    @app.exception_handler(CommandConflict)
    async def conflict(request, error):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.get("/health")
    async def health():
        return {"status": "ok", "runtime": "p2a", "media": "mock"}

    @app.get("/providers", response_model=list[ProviderView])
    async def providers():
        provider = owned_provider
        if provider is None and service.engines:
            provider = next(iter(service.engines.values())).llm_provider
        if provider is None:
            return []
        return [{"capability": (await provider.capabilities()).model_dump(mode="json"),
                 "healthy": await provider.health()}]

    @app.post("/projects", status_code=201, response_model=ProjectRecord)
    async def create_project(brief: CreateProjectInput):
        return service.create(brief.canonical_brief(), brief.creative_hints)

    @app.get("/projects", response_model=list[ProjectRecord])
    async def list_projects():
        return [service.repository.get(pid) for pid in service.repository.list_ids()]

    @app.get("/projects/{project_id}/studio", response_model=StudioSnapshot)
    async def studio(project_id: str):
        return studio_snapshot(service, project_id)

    @app.post("/projects/{project_id}/cancel", status_code=202, response_model=CommandAccepted)
    async def cancel_project(project_id: str):
        return await service.cancel(project_id)

    @app.get("/projects/{project_id}", response_model=ProjectSnapshot)
    async def project(project_id: str):
        return service.snapshot(project_id)

    @app.post("/projects/{project_id}/start", status_code=202, response_model=CommandAccepted)
    async def start(project_id: str):
        return service.start(project_id)

    @app.post("/projects/{project_id}/pause", status_code=202, response_model=CommandAccepted)
    async def pause(project_id: str):
        return service.pause(project_id)

    @app.post("/projects/{project_id}/resume", status_code=202, response_model=CommandAccepted)
    async def resume(project_id: str):
        return service.start(project_id, resume=True)

    @app.get("/projects/{project_id}/workflow", response_model=WorkflowSnapshot)
    async def workflow(project_id: str):
        engine = service.engine(project_id)
        events = engine.event_bus.events()
        return {"graph": engine.current_production.graph.model_dump(mode="json"),
                "event_cursor": events[-1].event_id if events else None}

    @app.get("/projects/{project_id}/shots/{shot_id}", response_model=Shot)
    async def shot(project_id: str, shot_id: str):
        engine = service.engine(project_id)
        result = next((s for s in engine.current_project.shots if s.shot_id == shot_id), None)
        if result is None:
            raise KeyError(shot_id)
        return result

    @app.get("/projects/{project_id}/reviews", response_model=list[HumanReviewRequest])
    async def reviews(project_id: str):
        return service.engine(project_id).human_gates.all()

    @app.post("/reviews/{review_id}/resolve", response_model=HumanReviewRequest)
    async def resolve(review_id: str, command: ReviewResolution):
        return service.resolve_review(review_id, command.approved, command.notes)

    @app.get("/jobs/{job_id}", response_model=GenerationJob)
    async def job(job_id: str, project_id: str | None = None):
        return service.locate("job", job_id, project_id)[1]

    @app.post("/jobs/{job_id}/cancel", status_code=202, response_model=GenerationJob)
    async def cancel(job_id: str, project_id: str | None = None):
        return service.cancel_job(job_id, project_id)

    @app.get("/artifacts/{artifact_id}", response_model=Artifact)
    async def artifact(artifact_id: str, project_id: str | None = None, version: int | None = Query(None, ge=1)):
        engine, _ = service.locate("artifact", artifact_id, project_id)
        result = engine.artifact_store.get(artifact_id, version)
        if result is None:
            raise KeyError(artifact_id)
        return result

    @app.get("/projects/{project_id}/events")
    async def events(project_id: str, request: Request, after: str | None = None, follow: bool = True):
        engine = service.engine(project_id)
        cursor = after or request.headers.get("last-event-id")
        history = engine.event_bus.events()
        index = 0
        if cursor:
            found = next((i for i, e in enumerate(history) if e.event_id == cursor), None)
            if found is None:
                raise HTTPException(409, "Unknown event cursor; reload the snapshot")
            index = found + 1

        async def stream():
            nonlocal index
            idle = 0
            while True:
                if await request.is_disconnected():
                    return
                batch = engine.event_bus.events()[index:]
                for event in batch:
                    yield f"id: {event.event_id}\nevent: {event.event_type.value}\ndata: {event.model_dump_json()}\n\n"
                    index += 1
                if not follow:
                    return
                idle += 1
                if idle % 40 == 0:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.25)
        return StreamingResponse(stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app
