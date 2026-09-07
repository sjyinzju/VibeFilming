"""Image and video multipart requests preserve uploaded reference bytes exactly."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json

import httpx
from fastapi import FastAPI, File, Form, UploadFile
from PIL import Image

from movie_agent.media import (
    ArtifactMultipartTransport,
    CameraMotionSpec,
    GenericImagePromptCompiler,
    GenericVideoPromptCompiler,
    ImageGenerationMode,
    ImageGenerationRequest,
    ImagePurpose,
    ImageReferenceBindingInput,
    MediaGenerationStrategy,
    MediaReferenceBinaryResolver,
    MultipartMediaEncoder,
    VideoGenerationMode,
    VideoGenerationRequest,
)
from movie_agent.domain import GenerationStrategyType
from tests.p2a_fakes import FakeReasoningProvider
from tests.test_contracts import sample_shot
from tests.test_p2a_api import service_at


def fixture_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (29, 17), (122, 51, 44)).save(buffer, "PNG")
    return buffer.getvalue()


def test_image_and_video_reference_multipart_sha256_roundtrip(tmp_path) -> None:
    async def scenario() -> None:
        service = service_at(tmp_path, FakeReasoningProvider())
        original = fixture_png()
        upload = service.reference_uploads.upload_draft(
            "draft_transport123", original, filename="真实参考.png", mime_type="image/png",
            binding=ImageReferenceBindingInput(
                reference_type="style", binding_scope="project", purpose="visual_style"
            ),
        )
        first_upload = service.reference_uploads.upload_draft(
            "draft_transport123", original, filename="first.png", mime_type="image/png",
            binding=ImageReferenceBindingInput(
                reference_type="first_frame", binding_scope="project", purpose="first_frame"
            ),
        )
        last_upload = service.reference_uploads.upload_draft(
            "draft_transport123", original, filename="last.png", mime_type="image/png",
            binding=ImageReferenceBindingInput(
                reference_type="last_frame", binding_scope="project", purpose="last_frame"
            ),
        )
        artifacts, binaries = service.reference_uploads._draft_stores("draft_transport123")
        encoder = MultipartMediaEncoder(MediaReferenceBinaryResolver(artifacts, binaries))
        received: list[dict] = []
        remote = FastAPI()

        async def capture(part: UploadFile, role: str) -> dict:
            content = await part.read()
            return {
                "role": role,
                "filename": part.filename,
                "mime": part.content_type,
                "type": part.headers.get("x-reference-type"),
                "artifact": part.headers.get("x-artifact-id"),
                "sha256": hashlib.sha256(content).hexdigest(),
            }

        @remote.post("/image")
        async def image_endpoint(request: str = Form(...), references: list[UploadFile] = File(...)):
            payload = json.loads(request)
            parts = [await capture(item, "reference") for item in references]
            received.append({"kind": "image", "request": payload, "parts": parts})
            return {"ok": True}

        @remote.post("/video")
        async def video_endpoint(
            request: str = Form(...),
            first_frame: UploadFile = File(...),
            last_frame: UploadFile = File(...),
            references: list[UploadFile] | None = File(default=None),
        ):
            payload = json.loads(request)
            parts = [await capture(first_frame, "first_frame"),
                     await capture(last_frame, "last_frame")]
            parts.extend([await capture(item, "reference") for item in (references or [])])
            received.append({"kind": "video", "request": payload, "parts": parts})
            return {"ok": True}

        shot = sample_shot()
        reference = upload.reference
        image_request = ImageGenerationRequest(
            job_id="image_transport", project_id="project_transport",
            prompt_package=GenericImagePromptCompiler().compile(
                shot, ImagePurpose.STYLE_FRAME, [reference]
            ),
            references=[reference], output_artifact_id="image_output",
            mode=ImageGenerationMode.REFERENCE_TO_IMAGE, purpose=ImagePurpose.STYLE_FRAME,
            width=1280, height=720, aspect_ratio="16:9",
        )
        strategy = MediaGenerationStrategy(
            strategy_type=GenerationStrategyType.FIRST_LAST_FRAME_TO_VIDEO,
            reason="transport test",
        )
        video_request = VideoGenerationRequest(
            job_id="video_transport", project_id="project_transport", shot_id=shot.shot_id,
            scene_id=shot.scene_id,
            prompt_package=GenericVideoPromptCompiler().compile(shot, strategy, [reference]),
            references=[reference], output_artifact_id="video_output",
            mode=VideoGenerationMode.FIRST_LAST_FRAME_TO_VIDEO,
            duration_seconds=2, fps=24, width=1280, height=720, aspect_ratio="16:9",
            first_frame=first_upload.reference,
            last_frame=last_upload.reference,
            camera_motion=CameraMotionSpec(motion_type="static"),
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=remote),
                                    base_url="http://remote") as client:
            transport = ArtifactMultipartTransport(client, encoder)
            await transport.post_image("/image", image_request)
            await transport.post_video("/video", video_request)

        expected_hash = hashlib.sha256(original).hexdigest()
        assert received[0]["request"]["references"][0]["artifact_id"] == upload.artifact_id
        assert received[0]["parts"] == [{
            "role": "reference", "filename": "真实参考.png", "mime": "image/png",
            "type": "style", "artifact": upload.artifact_id, "sha256": expected_hash,
        }]
        video_parts = received[1]["parts"]
        assert [part["role"] for part in video_parts] == ["first_frame", "last_frame", "reference"]
        assert [part["type"] for part in video_parts] == ["first_frame", "last_frame", "style"]
        assert all(part["sha256"] == expected_hash for part in video_parts)
        assert [part["artifact"] for part in video_parts] == [
            first_upload.artifact_id, last_upload.artifact_id, upload.artifact_id,
        ]

    asyncio.run(scenario())
