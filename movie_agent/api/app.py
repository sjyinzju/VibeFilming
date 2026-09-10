"""REST commands/snapshots and replayable server-sent events."""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Query, UploadFile, File, Form, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from movie_agent.domain import ProjectBrief, Shot, HumanReviewRequest, GenerationJob, Artifact
from movie_agent.application.service import ProductionService, CommandConflict
from movie_agent.application.repository import ProjectRecord
from movie_agent.application.views import ProjectSnapshot, WorkflowSnapshot, CommandAccepted, ProviderView
from movie_agent.application.creative_inputs import CreateProjectInput
from movie_agent.application.post_commands import PostExportCommand
from movie_agent.application.audio_commands import AudioProductionCommand
from movie_agent.quality.recovery import HumanReferenceAcceptance
from movie_agent.application.studio import StudioSnapshot, studio_snapshot
from movie_agent.media import (
    ImageReferenceBindingInput,
    ImageReferenceUploadResult,
    ImageUploadValidationError,
    MediaReference,
    ProviderCapabilities,
    ReferenceBindingScope,
    media_metadata,
    media_response,
    HumanRepairInput,
)


class ReviewResolution(BaseModel):
    """Explicit human approval/rejection command."""
    model_config = ConfigDict(extra="forbid")
    approved: bool
    notes: str | None = None
    media_directive: HumanRepairInput | None = None


class TerminalRevisionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_id: str
    authorization_reference: str


class RemoteVideoReplayCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    remote_prompt_id: str
    authorization_reference: str


def create_app(service: ProductionService | None = None, *, resource_runtime=None) -> FastAPI:
    """Compose local adapters by default; tests and future storage inject a service."""
    owned_provider = None
    if service is None:
        from movie_agent.config import LLMConfig
        from movie_agent.providers.openai_compatible import OpenAICompatibleLLMProvider
        from movie_agent.storage.projects import LocalProjectRepository
        from movie_agent.execution.durable_events import DurableLocalEventBus
        from movie_agent.application.production_factory import production_engine
        root = Path(os.environ.get("MOVIE_AGENT_WORKSPACE", "workspace/studio")).resolve()
        owned_provider = OpenAICompatibleLLMProvider(LLMConfig.from_env())
        from movie_agent.model_services.wiring import build_spark_runtime
        from movie_agent.application.runtime_configuration import production_media_settings
        media_settings = production_media_settings(root)
        resource_runtime = build_spark_runtime(owned_provider.config, media_settings)
        def factory(project_id):
            workspace = root / project_id
            return production_engine(workspace, owned_provider,
                event_bus=DurableLocalEventBus(workspace / "events"),
                media_settings=media_settings, runtime_coordinator=resource_runtime)
        service = ProductionService(LocalProjectRepository(root), factory)

    @asynccontextmanager
    async def lifespan(app):
        async def observe_resources():
            while True:
                try:
                    for model in resource_runtime.manager.all():
                        await model.status()
                    await resource_runtime.snapshot()
                    await resource_runtime.maintain()
                except Exception:
                    # A later admission independently fails closed on unavailable telemetry.
                    pass
                await asyncio.sleep(resource_runtime.settings.telemetry_interval)
        monitor = (asyncio.create_task(observe_resources())
                   if resource_runtime and resource_runtime.settings.enabled else None)
        try:
            yield
        finally:
            if monitor:
                from contextlib import suppress
                monitor.cancel()
                with suppress(asyncio.CancelledError):
                    await monitor
        await service.shutdown()
        if owned_provider:
            await owned_provider.aclose()
        if resource_runtime:
            resource_runtime.close()

    app = FastAPI(title="Movie Agent", version="0.3.0", lifespan=lifespan)
    app.state.production_service = service
    app.state.resource_runtime = resource_runtime

    @app.get("/runtime/resources")
    async def resources():
        return resource_runtime.view() if resource_runtime else {"enabled": False}

    @app.exception_handler(KeyError)
    async def not_found(request, error):
        return JSONResponse(status_code=404, content={"detail": "Resource not found"})

    @app.exception_handler(CommandConflict)
    async def conflict(request, error):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.exception_handler(ImageUploadValidationError)
    async def invalid_image(request, error):
        status_code = 413 if "limit" in str(error).lower() else 422
        return JSONResponse(status_code=status_code, content={"detail": str(error)})

    @app.get("/health")
    async def health():
        return {"status": "ok", "runtime": "film_production", "media": "configured_providers"}

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
        return service.create(brief.canonical_brief(), brief.creative_hints, brief.draft_id,
            production_policy=brief.production_policy)

    async def read_image(file: UploadFile) -> tuple[bytes, str]:
        mime_type = (file.content_type or "").lower()
        if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ImageUploadValidationError("Only PNG, JPEG, and WebP images are supported")
        content = await file.read(service.reference_uploads.max_size_bytes + 1)
        return content, mime_type

    def parse_binding(value: str | None) -> ImageReferenceBindingInput:
        if value is None:
            return ImageReferenceBindingInput(
                reference_type="style", binding_scope="project", purpose="visual_style"
            )
        try:
            return ImageReferenceBindingInput.model_validate_json(value)
        except ValueError as error:
            raise HTTPException(422, "Reference binding metadata is invalid") from error

    @app.post("/drafts/{draft_id}/image-references", status_code=201,
              response_model=ImageReferenceUploadResult)
    async def upload_draft_reference(
        draft_id: str,
        file: UploadFile = File(...),
        binding: str | None = Form(None),
    ):
        parsed = parse_binding(binding)
        if parsed.binding_scope not in {ReferenceBindingScope.PROJECT,
                                        ReferenceBindingScope.CREATIVE_INPUT}:
            raise HTTPException(422, "Draft references must bind to project or creative input")
        content, mime_type = await read_image(file)
        return service.reference_uploads.upload_draft(
            draft_id, content, filename=file.filename, mime_type=mime_type, binding=parsed
        )

    @app.get("/drafts/{draft_id}/image-references", response_model=list[MediaReference])
    async def list_draft_references(draft_id: str):
        return service.reference_uploads.list_draft(draft_id)

    @app.delete("/drafts/{draft_id}/image-references/{reference_id}", status_code=204)
    async def remove_draft_reference(draft_id: str, reference_id: str):
        service.reference_uploads.unbind_draft(draft_id, reference_id)
        return Response(status_code=204)

    @app.post('/projects/{project_id}/reference-acceptances',response_model=CommandAccepted)
    async def accept_reference_bundle(project_id: str, command: HumanReferenceAcceptance):
        from movie_agent.application.reference_commands import accept_references
        return accept_references(service,project_id,command)

    @app.post("/projects/{project_id}/image-references", status_code=201,
              response_model=ImageReferenceUploadResult)
    async def upload_project_reference(
        project_id: str,
        file: UploadFile = File(...),
        binding: str | None = Form(None),
    ):
        content, mime_type = await read_image(file)
        return service.add_image_reference(
            project_id, content, filename=file.filename, mime_type=mime_type,
            binding=parse_binding(binding),
        )

    @app.delete("/projects/{project_id}/image-references/{reference_id}", status_code=204)
    async def remove_project_reference(project_id: str, reference_id: str):
        service.remove_reference(project_id, reference_id)
        return Response(status_code=204)

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

    @app.post("/projects/{project_id}/revise-terminal", status_code=202, response_model=CommandAccepted)
    async def revise_terminal(project_id: str, command: TerminalRevisionCommand):
        return service.revise_terminal(project_id, command.scene_id, command.authorization_reference)

    @app.post("/projects/{project_id}/recover-video", status_code=202,
              response_model=CommandAccepted)
    async def recover_video(project_id: str, command: RemoteVideoReplayCommand):
        return service.recover_video_job(
            project_id,
            command.job_id,
            command.remote_prompt_id,
            command.authorization_reference,
        )

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
        return service.resolve_review(review_id, command.approved, command.notes, command.media_directive)

    @app.post("/projects/{project_id}/media-feedback", response_model=HumanReviewRequest)
    async def media_feedback(project_id: str, command: HumanRepairInput):
        return service.media_feedback(project_id, command)

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
        from movie_agent.media.download import public_artifact
        return public_artifact(result)

    @app.get("/projects/{project_id}/artifacts/{artifact_id}/versions/{version}/download")
    async def download_artifact(project_id: str, artifact_id: str, version: int):
        from movie_agent.media.download import ArtifactBinaryResolver, download_response
        if version < 1:
            raise HTTPException(422, 'Artifact version must be positive')
        engine = service.engine(project_id)
        artifact = engine.artifact_store.get(artifact_id, version)
        resolver = ArtifactBinaryResolver(engine.artifact_store,engine.binary_store)
        record = await asyncio.to_thread(resolver.resolve,artifact,project_id)
        return download_response(artifact,record)

    @app.get("/projects/{project_id}/exports/final")
    async def download_final(project_id: str):
        engine = service.engine(project_id)
        final = next((a for a in engine.artifact_store.list_versions('final_film') if a.selected),None)
        if not final or final.metadata.get('mock') is not False or not final.metadata.get('qc',{}).get('passed'):
            raise HTTPException(404,'No selected real final film')
        approval = final.provenance.parameters.get('approved_review_id')
        if not any(r.review_id==approval and r.status.value=='approved' for r in engine.human_gates.all()):
            raise HTTPException(409,'Final film has no recorded approval')
        return await download_artifact(project_id,final.artifact_id,final.version)

    @app.post("/projects/{project_id}/exports/reexport",status_code=202,response_model=CommandAccepted)
    async def reexport_final(project_id: str, command: PostExportCommand):
        from movie_agent.application.post_commands import reexport
        return reexport(service,project_id,command)

    @app.post('/projects/{project_id}/audio',status_code=202,response_model=CommandAccepted)
    async def produce_audio(project_id: str, command: AudioProductionCommand):
        from movie_agent.application.audio_commands import audio_command
        return audio_command(service,project_id,command)

    @app.post("/artifacts/{artifact_id}/select", response_model=Artifact)
    async def select_artifact(artifact_id: str, project_id: str | None = None,
                              version: int = Query(..., ge=1)):
        engine, _ = service.locate("artifact", artifact_id, project_id)
        try:
            from movie_agent.media.download import public_artifact
            return public_artifact(engine._select(engine.current_project, artifact_id, version))
        except KeyError:
            raise KeyError(artifact_id)

    def resolve_media_artifact(
        artifact_id: str,
        project_id: str | None,
        draft_id: str | None,
        version: int | None,
        view: str,
    ):
        if draft_id:
            if project_id:
                raise HTTPException(400, "Supply either project_id or draft_id")
            source, binary_store = service.reference_uploads.locate_draft_artifact(
                draft_id, artifact_id, version
            )
            get_artifact = lambda identity: service.reference_uploads.locate_draft_artifact(
                draft_id, identity
            )[0]
        else:
            engine, _ = service.locate("artifact", artifact_id, project_id)
            source = engine.artifact_store.get(artifact_id, version)
            if source is None:
                raise KeyError(artifact_id)
            binary_store = engine.binary_store
            get_artifact = lambda identity: engine.artifact_store.get(identity)
        metadata = media_metadata(source)
        if metadata is None:
            raise HTTPException(404, "Artifact is not playable media")
        target_id = artifact_id
        if view == "preview":
            target_id = metadata.preview_artifact_id or artifact_id
        elif view == "thumbnail":
            target_id = metadata.thumbnail_artifact_id or (
                artifact_id if source.artifact_type.value in {"image", "frame"} else ""
            )
        if not target_id:
            raise HTTPException(404, "Artifact has no thumbnail")
        target = source if target_id == artifact_id else get_artifact(target_id)
        if target is None:
            raise HTTPException(404, "Preview artifact is unavailable")
        return binary_store, target

    @app.get("/artifacts/{artifact_id}/content")
    async def artifact_content(request: Request, artifact_id: str,
                               project_id: str | None = None,
                               draft_id: str | None = None,
                               version: int | None = Query(None, ge=1)):
        binary_store, target = resolve_media_artifact(artifact_id, project_id, draft_id, version, "content")
        return media_response(request, target, binary_store)

    @app.get("/artifacts/{artifact_id}/preview")
    async def artifact_preview(request: Request, artifact_id: str,
                               project_id: str | None = None,
                               draft_id: str | None = None,
                               version: int | None = Query(None, ge=1)):
        binary_store, target = resolve_media_artifact(artifact_id, project_id, draft_id, version, "preview")
        return media_response(request, target, binary_store)

    @app.get("/artifacts/{artifact_id}/thumbnail")
    async def artifact_thumbnail(request: Request, artifact_id: str,
                                 project_id: str | None = None,
                                 draft_id: str | None = None,
                                 version: int | None = Query(None, ge=1)):
        binary_store, target = resolve_media_artifact(artifact_id, project_id, draft_id, version, "thumbnail")
        return media_response(request, target, binary_store)

    @app.get("/projects/{project_id}/media/providers", response_model=list[ProviderCapabilities])
    async def media_providers(project_id: str):
        return await service.engine(project_id).media_runtime.capabilities()

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
