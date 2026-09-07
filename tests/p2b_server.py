"""Explicit offline E2E composition: real API/Core/SSE, fake reasoning, Mock media."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import httpx
from fastapi import FastAPI, HTTPException, Request
from movie_agent.api.app import create_app as api_app
from movie_agent.domain import PromptPackage
from movie_agent.media import (
    ArtifactMultipartTransport,
    CameraMotionSpec,
    ImageGenerationMode,
    ImageGenerationRequest,
    ImagePurpose,
    MediaReferenceBinaryResolver,
    MultipartMediaEncoder,
    ReferenceType,
    VideoGenerationMode,
    VideoGenerationRequest,
)
from tests.p2a_fakes import FakeReasoningProvider
from tests.test_p2a_api import service_at


class StudioTestProvider(FakeReasoningProvider):
    async def submit(self, request):
        await asyncio.sleep(0.4)
        return await super().submit(request)


def create_app():
    root = Path(os.environ.get('MOVIE_AGENT_E2E_WORKSPACE', 'workspace/p2b-e2e'))
    service = service_at(root, StudioTestProvider())
    app = api_app(service)

    @app.post('/test/projects/{project_id}/reference-transport/{artifact_id}')
    async def reference_transport(project_id: str, artifact_id: str):
        """E2E-only fake remote: prove browser-upload bytes cross multipart unchanged."""
        engine = service.engine(project_id)
        reference = next((item for item in engine.reference_bank.all()
                          if item.artifact_id == artifact_id), None)
        if reference is None:
            raise HTTPException(404, 'Reference not found')
        receipts = []
        remote = FastAPI()

        @remote.post('/receive/{kind}')
        async def receive(kind: str, request: Request):
            form = await request.form()
            parts = []
            for field, value in form.multi_items():
                if field == 'request':
                    continue
                content = await value.read()
                parts.append({
                    'field': field,
                    'filename': value.filename,
                    'mime': value.content_type,
                    'reference_type': value.headers.get('x-reference-type'),
                    'role': value.headers.get('x-reference-role'),
                    'artifact_id': value.headers.get('x-artifact-id'),
                    'sha256': hashlib.sha256(content).hexdigest(),
                })
            receipt = {'kind': kind, 'request': json.loads(str(form['request'])), 'parts': parts}
            receipts.append(receipt)
            return {'received': len(parts)}

        prompt = PromptPackage(
            compiler_id='e2e-reference-transport', compiler_version='1',
            positive_prompt='transport contract verification',
        )
        image_request = ImageGenerationRequest(
            job_id='e2e-image', project_id=project_id, prompt_package=prompt,
            references=[reference], output_artifact_id='e2e-image-output',
            mode=ImageGenerationMode.REFERENCE_TO_IMAGE, purpose=ImagePurpose.STYLE_FRAME,
            width=1280, height=720, aspect_ratio='16:9',
        )
        video_request = VideoGenerationRequest(
            job_id='e2e-video', project_id=project_id, shot_id='e2e-shot',
            prompt_package=prompt, references=[reference], output_artifact_id='e2e-video-output',
            mode=VideoGenerationMode.FIRST_LAST_FRAME_TO_VIDEO,
            duration_seconds=2, fps=24, width=1280, height=720, aspect_ratio='16:9',
            first_frame=reference.model_copy(update={'reference_type': ReferenceType.FIRST_FRAME}),
            last_frame=reference.model_copy(update={'reference_type': ReferenceType.LAST_FRAME}),
            camera_motion=CameraMotionSpec(motion_type='static'),
        )
        encoder = MultipartMediaEncoder(
            MediaReferenceBinaryResolver(engine.artifact_store, engine.binary_store)
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=remote),
                                    base_url='http://fake-remote') as client:
            transport = ArtifactMultipartTransport(client, encoder)
            await transport.post_image('/receive/image', image_request)
            await transport.post_video('/receive/video', video_request)
        return {'expected_sha256': engine.artifact_store.get(artifact_id).metadata['sha256'],
                'receipts': receipts}

    return app
