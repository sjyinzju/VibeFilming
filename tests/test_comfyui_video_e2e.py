"""Full fake-HTTP ComfyUI bridge: immutable frame inputs to video plus native audio."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import wave
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, Response, UploadFile
from PIL import Image

from movie_agent.artifacts import LocalArtifactStore
from movie_agent.comfyui import (
    BindingValueType,
    ComfyUIClient,
    ComfyUIOutputDeclaration,
    ComfyUIPollingExecutionEventAdapter,
    ComfyUIWorkflowProfile,
    ComfyUIWorkflowRegistry,
    ComfyUIWorkflowTemplate,
    WorkflowBinding,
    WorkflowBindingManifest,
    compute_workflow_hash,
)
from movie_agent.domain import (
    Artifact,
    ArtifactType,
    EventType,
    GenerationJob,
    GenerationStrategyType,
    PromptPackage,
    ProviderKind,
    ResourceClass,
)
from movie_agent.execution import JobManager, LocalEventBus
from movie_agent.media import (
    CameraMotionSpec,
    LocalBinaryArtifactStore,
    MediaModality,
    MediaReference,
    MediaReferenceBinaryResolver,
    ReferenceType,
    ResourceProfile,
    VideoCapabilities,
    VideoGenerationMode,
    VideoGenerationRequest,
)
from movie_agent.media.runtime import MediaRuntime
from movie_agent.providers import ProviderRegistry
from movie_agent.providers.comfyui_video import ComfyUIVideoProvider
from movie_agent.providers.media import _MOCK_MP4


FIXTURES = Path(__file__).parent / "fixtures"


def png_bytes(colour: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 18), colour).save(buffer, "PNG")
    return buffer.getvalue()


def wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\x00\x00" * 800)
    return buffer.getvalue()


def workflow_registry() -> ComfyUIWorkflowRegistry:
    workflow = json.loads((FIXTURES / "comfyui_video_foundation_api.json").read_text("utf-8"))
    template = ComfyUIWorkflowTemplate(
        template_id="comfyui_video_foundation_fixture", version="1.0.0",
        modality=MediaModality.VIDEO,
        generation_mode=VideoGenerationMode.FIRST_LAST_FRAME_TO_VIDEO,
        api_workflow=workflow, template_hash=compute_workflow_hash(workflow),
        supported_capabilities=["first_frame", "last_frame", "first_last_frame", "audio_generation"],
        required_semantic_slots=[
            "prompt", "negative_prompt", "seed", "width", "height", "fps",
            "duration_seconds", "frame_count", "first_frame", "last_frame",
        ],
        outputs=[
            ComfyUIOutputDeclaration(
                node_id="20", modality=MediaModality.VIDEO, purpose="shot_video",
                history_key="videos", mime_type="video/mp4", extension="mp4",
                codec="h264", primary=True,
            ),
            ComfyUIOutputDeclaration(
                node_id="21", modality=MediaModality.AUDIO, purpose="native_audio",
                history_key="audio", mime_type="audio/wav", extension="wav",
                codec="pcm_s16le",
            ),
        ],
    )
    manifest = WorkflowBindingManifest(
        manifest_id="comfyui_video_foundation_fixture_bindings", version="1.0.0",
        template_id=template.template_id, template_version=template.version,
        bindings=[
            WorkflowBinding(semantic_slot=slot, node_id="10", input_name=slot, value_type=value_type, required=True)
            for slot, value_type in (
                ("prompt", BindingValueType.STRING),
                ("negative_prompt", BindingValueType.STRING),
                ("seed", BindingValueType.INTEGER),
                ("width", BindingValueType.INTEGER),
                ("height", BindingValueType.INTEGER),
                ("fps", BindingValueType.NUMBER),
                ("duration_seconds", BindingValueType.NUMBER),
                ("frame_count", BindingValueType.INTEGER),
                ("first_frame", BindingValueType.ASSET),
                ("last_frame", BindingValueType.ASSET),
            )
        ],
    )
    registry = ComfyUIWorkflowRegistry()
    registry.register_template(template)
    registry.register_manifest(manifest)
    registry.register_profile(ComfyUIWorkflowProfile(
        profile_id="foundation_fixture", template_id=template.template_id,
        template_version=template.version, binding_manifest_id=manifest.manifest_id,
        binding_manifest_version=manifest.version, model_profile="foundation-test-only",
    ))
    return registry


def register_image_versions(
    artifacts: LocalArtifactStore,
    binaries: LocalBinaryArtifactStore,
    artifact_id: str,
    versions: list[bytes],
) -> MediaReference:
    for version, content in enumerate(versions, 1):
        binary = binaries.put(artifact_id, version, content, mime_type="image/png", extension="png")
        artifacts.register(Artifact(
            artifact_id=artifact_id, artifact_type=ArtifactType.FRAME, uri=binary.uri,
            version=version, metadata={"mime_type": "image/png", "size_bytes": len(content),
                "width": 32, "height": 18},
        ))
    artifacts.select(artifact_id, len(versions))
    return MediaReference(
        reference_type=(ReferenceType.FIRST_FRAME if artifact_id == "first" else ReferenceType.LAST_FRAME),
        artifact_id=artifact_id,
    )


def test_video_provider_fake_http_e2e_preserves_frames_and_registers_video_audio(tmp_path: Path) -> None:
    async def scenario() -> None:
        artifacts = LocalArtifactStore(tmp_path / "artifacts")
        binaries = LocalBinaryArtifactStore(tmp_path / "media")
        first_bytes = png_bytes((20, 40, 60))
        last_bytes = png_bytes((80, 100, 120))
        first = register_image_versions(artifacts, binaries, "first", [png_bytes((1, 2, 3)), first_bytes])
        last = register_image_versions(artifacts, binaries, "last", [last_bytes])
        expected_hashes = {
            "first": hashlib.sha256(first_bytes).hexdigest(),
            "last": hashlib.sha256(last_bytes).hexdigest(),
        }

        app = FastAPI()
        uploads: dict[str, str] = {}
        submitted: dict[str, object] = {}
        job_calls = 0
        video_bytes, audio_bytes = _MOCK_MP4, wav_bytes()

        @app.get("/system_stats")
        async def stats():
            return {"system": {"comfyui_version": "0.3.fixture"}, "devices": []}

        @app.get("/object_info")
        async def object_info():
            inputs = {key: ["STRING", {}] for key in json.loads(
                (FIXTURES / "comfyui_video_foundation_api.json").read_text("utf-8")
            )["10"]["inputs"]}
            return {
                "MovieAgentVideoFixture": {"input": {"required": inputs}},
                "MovieAgentSaveVideoFixture": {"input": {"required": {"source": ["VIDEO", {}]}}},
                "MovieAgentSaveAudioFixture": {"input": {"required": {"source": ["AUDIO", {}]}}},
            }

        @app.get("/object_info/{node_class}")
        async def object_info_node(node_class: str):
            return {node_class: (await object_info())[node_class]}

        @app.post("/upload/image")
        async def upload(
            image: UploadFile = File(...), type: str = Form(...),
            subfolder: str = Form(""), overwrite: str = Form("false"),
        ):
            content = await image.read()
            uploads[image.filename] = hashlib.sha256(content).hexdigest()
            return {"name": image.filename, "subfolder": subfolder, "type": type}

        @app.post("/prompt")
        async def prompt(body: dict):
            submitted.update(body)
            return {"prompt_id": "prompt_e2e", "number": 1, "node_errors": {}}

        @app.get("/api/jobs/{job_id}")
        async def job(job_id: str):
            nonlocal job_calls
            states = ["pending", "in_progress", "completed"]
            state = states[min(job_calls, len(states) - 1)]
            job_calls += 1
            return {"id": job_id, "status": state}

        @app.get("/history/{prompt_id}")
        async def history(prompt_id: str):
            return {prompt_id: {"outputs": {
                "20": {"videos": [{"filename": "clip.mp4", "subfolder": "out", "type": "output"}]},
                "21": {"audio": [{"filename": "native.wav", "subfolder": "out", "type": "output"}]},
            }, "status": {"status_str": "success", "completed": True, "messages": []}}}

        @app.get("/view")
        async def view(filename: str, subfolder: str = "", type: str = "output"):
            return Response(
                content=audio_bytes if filename.endswith(".wav") else video_bytes,
                media_type="audio/wav" if filename.endswith(".wav") else "video/mp4",
            )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://comfyui"
        ) as http:
            client = ComfyUIClient(endpoint="http://comfyui", http_client=http)
            provider = ComfyUIVideoProvider(
                client=client,
                workflows=workflow_registry(),
                workflow_profile="foundation_fixture",
                resolver=MediaReferenceBinaryResolver(artifacts, binaries),
                runtime_capabilities=VideoCapabilities(
                    first_frame=True, last_frame=True, first_last_frame=True,
                    audio_generation=True, max_duration_seconds=10,
                    max_width=1344, max_height=1344, supported_fps=[24],
                ),
                execution_adapter=ComfyUIPollingExecutionEventAdapter(poll_interval=0),
                test_mode=True,
            )
            providers = ProviderRegistry()
            providers.register(provider)
            events = LocalEventBus()
            from tests.resource_fakes import fake_resource_runtime
            runtime = MediaRuntime(artifacts, binaries, providers, events, "trace_e2e",
                                   runtime_coordinator=fake_resource_runtime(provider.provider_id))
            jobs = JobManager(events, "trace_e2e")
            runtime.bind_jobs(jobs)
            request = VideoGenerationRequest(
                job_id="job_e2e", project_id="project_e2e", scene_id="scene_e2e",
                shot_id="shot_e2e", output_artifact_id="video_e2e",
                prompt_package=PromptPackage(
                    compiler_id="generic_video", compiler_version="1.0.0",
                    positive_prompt="A complete HTTP bridge", negative_prompt="flicker",
                ),
                references=[first, last], first_frame=first, last_frame=last,
                mode=VideoGenerationMode.FIRST_LAST_FRAME_TO_VIDEO,
                duration_seconds=2, fps=24, width=1920, height=1080,
                aspect_ratio="16:9", seed=None,
                camera_motion=CameraMotionSpec(
                    motion_type="static", direction="none", speed="N/A"
                ),
            )
            job_record = GenerationJob(
                job_id=request.job_id, project_id=request.project_id, scene_id=request.scene_id,
                shot_id=request.shot_id, node_id="shot_production", task="video",
                strategy_type=GenerationStrategyType.FIRST_LAST_FRAME_TO_VIDEO.value,
                resource_class=ResourceClass.MEDIUM, idempotency_key=request.job_id,
                input_artifact_ids=[first.artifact_id, last.artifact_id],
            )
            video = await runtime.generate_video(job_record, request)

        assert set(uploads.values()) == set(expected_hashes.values())
        assert len(uploads) == 2
        compiled = submitted["prompt"]["10"]["inputs"]
        assert compiled["first_frame"].startswith("movie-agent/first_v2_")
        assert compiled["last_frame"].startswith("movie-agent/last_v1_")
        assert compiled["frame_count"] == 48
        assert (compiled["width"], compiled["height"]) == (32, 18)

        audio = artifacts.get("video_e2e_native_audio")
        assert video.artifact_type == ArtifactType.VIDEO and audio is not None
        assert video.source_job_id == audio.source_job_id == "job_e2e"
        with binaries.open(video.uri) as stream:
            assert stream.read() == video_bytes
        with binaries.open(audio.uri) as stream, wave.open(stream, "rb") as decoded:
            assert decoded.getframerate() == 8000 and decoded.getnchannels() == 1
        completed_job = jobs.get("job_e2e")
        assert completed_job.output_artifact_ids == ["video_e2e", "video_e2e_native_audio"]
        assert completed_job.remote_status.value == "succeeded"
        assert compiled["seed"] == completed_job.provenance.seed == video.provenance.seed
        assert completed_job.provenance.parameters["requested_delivery_dimensions"]["width"] == 1920
        assert completed_job.provenance.parameters["effective_generation_dimensions"] == {
            "schema_version": "1.0.0", "width": 32, "height": 18, "aspect_ratio": "16:9"
        }
        assert "existing boundary frame canvas" in " ".join(
            completed_job.provenance.parameters["adaptation_reason"]
        )
        assert video.provenance.parameters["effective_generation_dimensions"]["height"] == 18
        assert video.provenance.parameters["workflow_template_id"] == "comfyui_video_foundation_fixture"
        inputs = video.provenance.parameters["input_artifacts"]
        assert {(item["artifact_id"], item["version"], item["sha256"]) for item in inputs} == {
            ("first", 2, expected_hashes["first"]), ("last", 1, expected_hashes["last"]),
        }
        remote_states = [event.payload.get("remote_status") for event in events.events(EventType.MEDIA_JOB_PROGRESS)]
        assert remote_states == ["queued", "running", "succeeded"]

    asyncio.run(scenario())
