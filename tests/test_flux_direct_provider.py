"""Offline adapter/transport tests; no GPU, SSH, downloads, or real generation."""
import asyncio
import hashlib
import io
import json

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import Response
from PIL import Image

from movie_agent.artifacts import LocalArtifactStore
from movie_agent.domain import Artifact, ArtifactType, ProviderErrorType
from movie_agent.media import (
    ImageGenerationMode, ImageGenerationRequest, ImagePurpose, LocalBinaryArtifactStore,
    MediaReference, ReferenceType, GenericImagePromptCompiler,
)
from movie_agent.media.transport import MediaReferenceBinaryResolver
from movie_agent.providers.base import ProviderFailure
from movie_agent.providers.flux_direct import FluxDirectImageProvider
from movie_agent.providers.registry import MediaProviderSettings, ProviderFactory
from tests.test_contracts import sample_shot


def image_request(**updates):
    values = dict(job_id="frame-job", project_id="project-flux", output_artifact_id="frame-output",
                  prompt_package=GenericImagePromptCompiler().compile(sample_shot(), ImagePurpose.FIRST_FRAME, []),
                  mode=ImageGenerationMode.TEXT_TO_IMAGE, purpose=ImagePurpose.FIRST_FRAME,
                  width=512, height=288, aspect_ratio="16:9", seed=42)
    return ImageGenerationRequest(**(values | updates))


def png(width=512, height=288):
    output = io.BytesIO()
    Image.new("RGB", (width, height), "blue").save(output, "PNG")
    return output.getvalue()


class FakeFlux:
    def __init__(self):
        self.app = FastAPI()
        self.calls = []
        self.error = None
        self.corrupt = False

        @self.app.get("/health")
        async def health():
            return {"api_version": "1.0", "service": "flux-direct-image", "model": "FLUX.1-dev", "ready": True}

        @self.app.post("/v1/images/generate")
        async def generate(request: Request):
            form = await request.form()
            body = json.loads(form["request_json"])
            source = form.get("source_image")
            self.calls.append((body, await source.read() if source else None, list(form)))
            if self.error:
                return Response(json.dumps({"error": {"code": self.error, "message": "private backend details"}}),
                                status_code=503, media_type="application/json")
            content = b"bad" if self.corrupt else png(body["width"], body["height"])
            return Response(content, media_type="image/png", headers={
                "X-Request-Id": "fake-service-request", "X-Flux-SHA256": hashlib.sha256(content).hexdigest(),
                "X-Flux-Seed": str(body["seed"]), "X-Flux-Mode": body["mode"],
                "X-Flux-Width": str(body["width"]), "X-Flux-Height": str(body["height"]),
                "X-Flux-Inference-Seconds": "1.25", "X-Flux-Generation-Seconds": "1.4",
                "X-Flux-Model-Load-Seconds": "10.0", "X-Flux-Resources": "{}",
            })

    def provider(self, resolver=None):
        return FluxDirectImageProvider(resolver=resolver, transport=httpx.ASGITransport(app=self.app))


def test_serialization_and_png_response():
    async def run():
        fake = FakeFlux()
        provider = fake.provider()
        request = image_request()
        response = await provider.generate(request)
        body, source, fields = fake.calls[0]
        assert fields == ["request_json"] and source is None
        assert body["mode"] == "TEXT_TO_IMAGE" and body["steps"] == 28
        assert request.prompt_package.positive_prompt in body["prompt"]
        assert request.prompt_package.negative_prompt in body["prompt"]
        assert response.payloads[0].content == png()
        assert response.result.provider_id == "flux_direct"
        assert response.result.provider_metadata["mock"] is False
        assert response.result.provider_metadata["model"] == "FLUX.1-dev"
        assert await provider.status(request.request_id) == response.result
        assert not await provider.cancel(request.request_id)
        caps = await provider.capabilities()
        assert caps.image.text_to_image and caps.image.image_to_image
        assert not caps.image.multi_reference and not caps.image.image_edit and not caps.supports_cancellation
    asyncio.run(run())


def test_source_artifact_bytes_survive_multipart(tmp_path):
    async def run():
        artifacts = LocalArtifactStore(tmp_path / "artifacts")
        binaries = LocalBinaryArtifactStore(tmp_path / "media")
        source = png(300, 300)
        stored = binaries.put("uploaded", 1, source, mime_type="image/png", extension="png")
        artifacts.register(Artifact(artifact_id="uploaded", artifact_type=ArtifactType.IMAGE,
                                   uri=stored.uri, metadata={"mime_type": "image/png"}))
        fake = FakeFlux()
        provider = fake.provider(MediaReferenceBinaryResolver(artifacts, binaries))
        ref = MediaReference(reference_type=ReferenceType.SOURCE_IMAGE, artifact_id="uploaded")
        response = await provider.generate(image_request(mode=ImageGenerationMode.IMAGE_TO_IMAGE, source_image=ref))
        assert fake.calls[0][1] == source
        assert fake.calls[0][0]["strength"] == 0.6
        assert response.result.provenance.input_artifact_ids == ["uploaded"]
        assert response.result.provider_metadata["source_artifact_uri"] == "artifact://uploaded/v1"
    asyncio.run(run())


@pytest.mark.parametrize("updates", [
    {"mode": ImageGenerationMode.INPAINT},
    {"references": [MediaReference(reference_type=ReferenceType.STYLE, artifact_id="style")]},
    {"provider_parameters": {"lora": "anything"}},
    {"mask_artifact_id": "mask"},
    {"mode": ImageGenerationMode.IMAGE_TO_IMAGE,
     "source_image": MediaReference(reference_type=ReferenceType.CHARACTER, artifact_id="character")},
])
def test_unsupported_never_silently_ignored(updates):
    async def run():
        fake = FakeFlux()
        with pytest.raises(ProviderFailure) as error:
            await fake.provider().generate(image_request(**updates))
        assert error.value.error_type == ProviderErrorType.UNSUPPORTED_CAPABILITY
        assert not fake.calls
    asyncio.run(run())


@pytest.mark.parametrize("code,kind", [
    ("MODEL_NOT_READY", ProviderErrorType.MODEL_NOT_READY),
    ("RESOURCE_EXHAUSTED", ProviderErrorType.RESOURCE_EXHAUSTED),
    ("TIMEOUT", ProviderErrorType.TIMEOUT),
    ("UNSUPPORTED_CAPABILITY", ProviderErrorType.UNSUPPORTED_CAPABILITY),
])
def test_remote_errors_are_safe_and_not_automatically_retried(code, kind):
    async def run():
        fake = FakeFlux()
        fake.error = code
        with pytest.raises(ProviderFailure) as error:
            await fake.provider().generate(image_request())
        assert error.value.error_type == kind and not error.value.retryable
        assert "private" not in str(error.value)
    asyncio.run(run())


def test_corrupt_success_is_rejected():
    async def run():
        fake = FakeFlux()
        fake.corrupt = True
        with pytest.raises(ProviderFailure) as error:
            await fake.provider().generate(image_request())
        assert error.value.error_type == ProviderErrorType.MEDIA_CORRUPT
    asyncio.run(run())


def test_config_and_factory_binding(tmp_path):
    settings = MediaProviderSettings.from_env({"MOVIE_AGENT_IMAGE_PROVIDER": "flux_direct",
        "MOVIE_AGENT_FLUX_ENDPOINT": "http://127.0.0.1:9991", "MOVIE_AGENT_FLUX_TIMEOUT": "650"},
        path=tmp_path / "absent.env")
    provider = ProviderFactory.defaults().build_registry(settings).get("flux_direct")
    assert provider.endpoint == "http://127.0.0.1:9991" and provider.timeout == 650
    assert not any(p.provider_id == "mock-image" for p in ProviderFactory.defaults(settings=settings).build_registry(settings).all())


def test_unavailable_and_comfyui_have_no_mock_fallback():
    async def run():
        def unavailable(request):
            raise httpx.ConnectError("private address", request=request)
        provider = FluxDirectImageProvider(transport=httpx.MockTransport(unavailable))
        assert not await provider.health()
        with pytest.raises(ProviderFailure) as error:
            await provider.generate(image_request())
        assert error.value.error_type == ProviderErrorType.UNAVAILABLE
        settings = MediaProviderSettings(image_provider="comfyui")
        comfy = ProviderFactory.defaults(settings=settings).build_registry(settings).get("comfyui")
        with pytest.raises(ProviderFailure, match="PROVIDER_UNAVAILABLE"):
            await comfy.generate(image_request())
    asyncio.run(run())


def test_frame_planner_uses_single_source_and_preserves_rejections():
    from movie_agent.media import MediaFramePlanner
    caps = asyncio.run(FakeFlux().provider().capabilities()).image
    planner = MediaFramePlanner()
    def plan(refs):
        return planner.plan("project", sample_shot(), width=1920, height=1080,
                            aspect_ratio="16:9", references=refs, capabilities=caps)[0]
    plain = plan([])
    assert (plain.first_frame_request.width, plain.first_frame_request.height) == (1024, 576)
    assert plain.requested_dimensions.width == 1920
    assert plain.first_frame_request.mode == ImageGenerationMode.TEXT_TO_IMAGE
    assert plain.last_frame_request.source_image.artifact_id == plain.first_frame_request.output_artifact_id
    source = MediaReference(reference_type=ReferenceType.SOURCE_IMAGE, artifact_id="uploaded")
    sourced = plan([source])
    assert sourced.first_frame_request.source_image == source
    assert sourced.last_frame_request.source_image.artifact_id == sourced.first_frame_request.output_artifact_id
    for refs in ([source, source.model_copy(update={"artifact_id": "second"})],
                 [source.model_copy(update={"reference_type": ReferenceType.CHARACTER})]):
        rejected = plan(refs).first_frame_request
        assert rejected.references == refs and rejected.source_image is None
        assert rejected.required_capabilities[0].capability == "multi_reference"


def test_workflow_image_artifacts_survive_checkpoint_resume(tmp_path):
    from pathlib import Path
    from movie_agent.media import MediaModality
    from movie_agent.services.mock_production import MockMovieProduction
    async def run():
        fake = FakeFlux()
        provider = fake.provider()
        settings = MediaProviderSettings(image_provider="flux_direct")
        factory = ProviderFactory.defaults()
        factory.register(MediaModality.IMAGE, "flux_direct", lambda: provider)
        engine = MockMovieProduction(tmp_path, media_settings=settings, media_provider_factory=factory)
        provider.resolver = MediaReferenceBinaryResolver(engine.artifact_store, engine.binary_store)
        result = await engine.run(engine.load_brief(Path(__file__).parent / "fixtures/sample_brief.json"),
                                  stop_after_node="storyboard_planning")
        frames = [item for item in result.artifacts if item.artifact_type == ArtifactType.FRAME]
        assert frames and all(item.provenance.provider_id == "flux_direct" for item in frames)
        before = {item.uri: engine.binary_store.open(item.uri).read() for item in frames}
        assert all(not item.metadata["mock"] and item.provenance.parameters["model"] == "FLUX.1-dev" for item in frames)
        last = next(item for item in frames if item.metadata["media"]["purpose"] == "last_frame")
        assert last.parent_artifact_ids and last.provenance.parameters["source_artifact_uri"].endswith("/v1")
        assert all(job.output_artifact_ids for job in result.jobs if job.task == "frame")
        count = len(fake.calls)
        resumed = MockMovieProduction(tmp_path, media_settings=settings, media_provider_factory=factory)
        provider.resolver = MediaReferenceBinaryResolver(resumed.artifact_store, resumed.binary_store)
        assert (await resumed.run(resume=True)).completed
        assert len(fake.calls) == count
        assert all(resumed.binary_store.open(uri).read() == data for uri, data in before.items())
    asyncio.run(run())


def test_routing_failure_is_a_visible_failed_job(tmp_path):
    from movie_agent.media.runtime import MediaRuntime
    from movie_agent.domain import GenerationJob, JobStatus
    from movie_agent.execution import JobManager, LocalEventBus
    async def run():
        events = LocalEventBus()
        settings = MediaProviderSettings(image_provider="comfyui")
        runtime = MediaRuntime(LocalArtifactStore(tmp_path / "artifacts"), LocalBinaryArtifactStore(tmp_path / "media"),
            ProviderFactory.defaults(settings=settings).build_registry(settings), events, "trace")
        manager = JobManager(events, "trace")
        runtime.bind_jobs(manager)
        from movie_agent.media import MediaCapabilityRequirement
        req = image_request(required_capabilities=[MediaCapabilityRequirement(capability="text_to_image")])
        job = GenerationJob(job_id=req.job_id, project_id=req.project_id, node_id="storyboard_planning",
                            task="frame", idempotency_key=req.job_id)
        with pytest.raises(RuntimeError):
            await runtime.generate_image(job, req)
        saved = manager.get(job.job_id)
        assert saved.status == JobStatus.FAILED and saved.provider_id == "comfyui"
        assert "unavailable" in saved.failure_reason and not saved.output_artifact_ids
    asyncio.run(run())


def test_api_previews_registered_flux_png_and_reports_mixed_mode(tmp_path):
    from movie_agent.api.app import create_app
    from movie_agent.application.service import ProductionService
    from movie_agent.storage.projects import LocalProjectRepository
    from movie_agent.services.reasoning_production import ReasoningMovieProduction
    from movie_agent.media import MediaModality
    from tests.p2a_fakes import FakeReasoningProvider
    from tests.test_p2a_runtime import brief
    async def run():
        fake = FakeFlux()
        def factory(pid):
            provider = fake.provider()
            providers = ProviderFactory.defaults()
            providers.register(MediaModality.IMAGE, "flux_direct", lambda: provider)
            engine = ReasoningMovieProduction(tmp_path / pid, FakeReasoningProvider(),
                media_settings=MediaProviderSettings(image_provider="flux_direct"), media_provider_factory=providers)
            provider.resolver = MediaReferenceBinaryResolver(engine.artifact_store, engine.binary_store)
            return engine
        service = ProductionService(LocalProjectRepository(tmp_path), factory, auto_approve=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service)), base_url="http://test") as client:
            pid = (await client.post("/projects", json=brief().model_dump(mode="json"))).json()["project"]["project_id"]
            await client.post(f"/projects/{pid}/start")
            await service.tasks[pid]
            snapshot = (await client.get(f"/projects/{pid}/studio")).json()
            assert snapshot["media_mode"] == "mixed" and "mock-image" not in snapshot["media_provider_ids"]
            frame = next(item for item in snapshot["artifacts"] if item["artifact_type"] == "frame")
            preview = next(item for item in snapshot["media_previews"] if item["artifact_id"] == frame["artifact_id"])
            response = await client.get(preview["preview_url"])
            assert response.status_code == 200 and response.headers["content-type"].startswith("image/png")
            assert hashlib.sha256(response.content).hexdigest() == frame["provenance"]["parameters"]["sha256"]
    asyncio.run(run())
