"""ComfyUI provider configuration, capability intersection, and routing failures."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from movie_agent.comfyui import ComfyUIClient, ComfyUIWorkflowRegistry
from movie_agent.domain import EventType, GenerationJob, JobStatus, PromptPackage, ProviderErrorType, ResourceClass
from movie_agent.execution import JobManager, LocalEventBus
from movie_agent.media import (
    CameraMotionSpec,
    MediaModality,
    MediaRoutingRequest,
    VideoCapabilities,
    VideoGenerationMode,
    VideoGenerationRequest,
)
from movie_agent.providers import MediaProviderSettings, MediaRouter, ProviderFactory, ProviderFailure
from movie_agent.providers.comfyui_video import ComfyUIVideoProvider
from movie_agent.providers.registry import MediaRoutingFailure
from tests.test_comfyui_video_e2e import workflow_registry


def test_factory_configures_comfyui_video_and_unavailable_endpoint_never_falls_back() -> None:
    async def scenario() -> None:
        settings = MediaProviderSettings.from_env({
            "MOVIE_AGENT_VIDEO_PROVIDER": "comfyui",
            "MOVIE_AGENT_COMFYUI_ENDPOINT": "http://127.0.0.1:18188",
            "MOVIE_AGENT_COMFYUI_TIMEOUT": "123",
            "MOVIE_AGENT_COMFYUI_WEBSOCKET_TIMEOUT": "456",
            "MOVIE_AGENT_COMFYUI_WORKFLOW_PROFILE": "minimax_h3_fl2va",
        })

        async def unavailable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline fixture", request=request)

        http = httpx.AsyncClient(
            transport=httpx.MockTransport(unavailable), base_url=settings.comfyui_endpoint
        )
        try:
            client = ComfyUIClient(
                endpoint=settings.comfyui_endpoint,
                timeout=settings.comfyui_timeout,
                websocket_timeout=settings.comfyui_websocket_timeout,
                http_client=http,
            )
            registry = ProviderFactory.defaults(
                settings=settings,
                resolver=None,
                comfyui_client=client,
                comfyui_workflows=ComfyUIWorkflowRegistry(),
                comfyui_runtime_capabilities=VideoCapabilities(first_frame=True),
            ).build_registry(settings)
            video_providers = [
                item for item in registry.all()
                if MediaModality.VIDEO in (await item.capabilities()).modalities
            ]
            assert [item.provider_id for item in video_providers] == ["comfyui-video"]
            with pytest.raises(MediaRoutingFailure) as captured:
                await MediaRouter(registry).select(MediaRoutingRequest(
                    modality=MediaModality.VIDEO,
                    task="video",
                    required_capabilities=["first_frame"],
                    resource_class=ResourceClass.MEDIUM,
                ))
            assert captured.value.error_type == ProviderErrorType.UNAVAILABLE
            assert "PROVIDER_UNAVAILABLE" in str(captured.value)
        finally:
            await http.aclose()

    asyncio.run(scenario())


def test_effective_capabilities_are_runtime_intersected_with_selected_workflow() -> None:
    async def scenario() -> None:
        async def healthy(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"system": {"comfyui_version": "fixture"}, "devices": []})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(healthy), base_url="http://comfyui"
        ) as http:
            provider = ComfyUIVideoProvider(
                client=ComfyUIClient(endpoint="http://comfyui", http_client=http),
                workflows=workflow_registry(), workflow_profile="foundation_fixture",
                resolver=None,
                runtime_capabilities=VideoCapabilities(
                    first_frame=True, last_frame=True, first_last_frame=True,
                    audio_generation=False,
                ),
            )
            capability = await provider.capabilities()
            assert capability.video.first_frame and capability.video.first_last_frame
            assert not capability.video.audio_generation

    asyncio.run(scenario())


def test_local_cancel_request_remote_dispatch_and_remote_confirmation_are_distinct() -> None:
    events = LocalEventBus()
    jobs = JobManager(events, "trace_cancel")
    jobs.add(GenerationJob(
        job_id="job_cancel", project_id="project_cancel", task="video",
        idempotency_key="job_cancel",
    ))
    jobs.transition("job_cancel", JobStatus.QUEUED)
    jobs.transition("job_cancel", JobStatus.PREPARING)
    jobs.transition("job_cancel", JobStatus.RUNNING)

    local = jobs.request_cancel("job_cancel")
    assert local.cancellation_requested and not local.remote_cancellation_dispatched
    assert local.remote_status is None and local.status == JobStatus.RUNNING

    dispatched = jobs.mark_remote_cancel_dispatched("job_cancel", True)
    assert dispatched.remote_cancellation_dispatched and dispatched.remote_status is None

    confirmed = jobs.provider_activity(
        "job_cancel", remote_status=JobStatus.CANCELLED,
        activity="ComfyUI execution interrupted",
    )
    assert confirmed.remote_status == JobStatus.CANCELLED
    jobs.transition("job_cancel", JobStatus.CANCELLED)
    assert events.events(EventType.MEDIA_JOB_CANCELLED)


def test_reserved_h3_profile_fails_as_unsupported_until_official_workflow_is_registered() -> None:
    async def scenario() -> None:
        async def healthy(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"system": {"comfyui_version": "fixture"}})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(healthy), base_url="http://comfyui"
        ) as http:
            provider = ComfyUIVideoProvider(
                client=ComfyUIClient(endpoint="http://comfyui", http_client=http),
                workflows=ComfyUIWorkflowRegistry(), workflow_profile="minimax_h3_fl2va",
                resolver=None,
            )
            request = VideoGenerationRequest(
                job_id="h3_job", project_id="h3_project", scene_id="h3_scene",
                shot_id="h3_shot", output_artifact_id="h3_video",
                prompt_package=PromptPackage(
                    compiler_id="generic", compiler_version="1.0.0", positive_prompt="future H3"
                ),
                mode=VideoGenerationMode.TEXT_TO_VIDEO, duration_seconds=1, fps=24,
                width=640, height=360, aspect_ratio="16:9",
                camera_motion=CameraMotionSpec(motion_type="static"),
            )
            with pytest.raises(ProviderFailure) as captured:
                await provider.generate(request)
            assert getattr(captured.value, "error_type", None) == ProviderErrorType.UNSUPPORTED_CAPABILITY

    asyncio.run(scenario())
