"""Explicit offline E2E composition: real API/Core/SSE, fake reasoning, Mock media."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import httpx
from fastapi import FastAPI, HTTPException, Request
from movie_agent.api.app import create_app as api_app
from movie_agent.comfyui.profiles import minimax_h3_fl2va_components
from movie_agent.domain import EventEnvelope, EventType, PromptPackage
from movie_agent.media import (
    ArtifactMultipartTransport,
    CameraMotionSpec,
    ImageGenerationMode,
    ImageGenerationRequest,
    ImagePurpose,
    MediaReferenceBinaryResolver,
    MultipartMediaEncoder,
    ReferenceType,
    ProviderExecutionGraphUpdate,
    ProviderExecutionNodeState,
    VideoGenerationMode,
    VideoGenerationRequest,
    build_provider_execution_graph,
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

    @app.post('/test/projects/{project_id}/comfyui-execution/{phase}')
    async def comfyui_execution(project_id: str, phase: str):
        """E2E-only ComfyUI event fixture exercising the production SSE path."""
        engine = service.engine(project_id)
        _, template, manifest = minimax_h3_fl2va_components()
        execution_id = f'e2e-comfyui-{project_id}'
        graph = build_provider_execution_graph(
            execution_id=execution_id,
            provider='comfyui-video',
            workflow_template_id=template.template_id,
            workflow_template_version=template.version,
            workflow_template_hash=template.template_hash,
            binding_manifest_id=manifest.manifest_id,
            binding_manifest_version=manifest.version,
            prompt={node_id: node.model_dump(mode='json')
                    for node_id, node in template.api_workflow.items()},
            node_metadata=template.node_metadata,
            parent_node_id='shot_production',
            parent_job_id='e2e-comfyui-video',
            project_id=project_id,
            scene_id='scene-e2e',
            shot_id='shot-e2e',
        )
        updates = {
            'start': ProviderExecutionGraphUpdate(
                execution_id=execution_id, remote_event='execution_start',
                remote_node_id='105:6', runtime_state=ProviderExecutionNodeState.RUNNING,
                activity='Loading H3 model',
            ),
            'sampling': ProviderExecutionGraphUpdate(
                execution_id=execution_id, remote_event='progress_state',
                remote_node_id='105:14', runtime_state=ProviderExecutionNodeState.RUNNING,
                progress=0.43, progress_is_determinate=True, activity='Sampling',
            ),
            'success': ProviderExecutionGraphUpdate(
                execution_id=execution_id, remote_event='execution_success',
            ),
        }
        if phase not in updates:
            raise HTTPException(400, 'Unknown execution phase')
        payload = {'provider_execution_update': updates[phase].model_dump(mode='json')}
        if phase == 'start':
            payload['provider_execution_graph'] = graph.model_dump(mode='json')
        engine.event_bus.emit(EventEnvelope(
            event_type=EventType.MEDIA_JOB_PROGRESS,
            project_id=project_id,
            trace_id=engine.trace_id,
            node_id='shot_production',
            job_id='e2e-comfyui-video',
            payload=payload,
        ))
        return {'execution_id': execution_id, 'phase': phase,
                'node_count': len(graph.nodes)}

    return app
