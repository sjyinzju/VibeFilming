"""Aggregated, deterministic video request preflight and safe adaptation."""

from __future__ import annotations

from datetime import UTC, datetime

from movie_agent.comfyui import ComfyUIWorkflowRegistry, VideoGenerationPreflight
from movie_agent.domain import (
    Artifact,
    ArtifactType,
    GenerationStrategyType,
    GenerationJob,
    EventType,
    JobStatus,
    PromptPackage,
    PromptSection,
    ProviderKind,
    Provenance,
    ResourceClass,
)
from movie_agent.media import (
    CameraMotionSpec,
    MediaCapabilityRequirement,
    MediaGenerationStrategy,
    MediaModality,
    MediaReference,
    ProviderCapabilities,
    ReferenceType,
    ResourceProfile,
    TemporalControl,
    VideoCapabilities,
    VideoGenerationMode,
    VideoGenerationRequest,
)
from movie_agent.services import MockMovieProduction


def capabilities() -> ProviderCapabilities:
    return ProviderCapabilities(
        provider_id="comfyui-video",
        kind=ProviderKind.VIDEO,
        modalities=[MediaModality.VIDEO],
        tasks=["video"],
        video=VideoCapabilities(
            first_frame=True,
            last_frame=True,
            first_last_frame=True,
            audio_generation=True,
            max_duration_seconds=15,
            max_width=1344,
            max_height=1344,
            supported_fps=[24],
        ),
        resource_profiles=[ResourceProfile(resource_class=ResourceClass.MEDIUM)],
    )


def strategy(*, required: list[str] | None = None) -> MediaGenerationStrategy:
    return MediaGenerationStrategy(
        strategy_type=GenerationStrategyType.FIRST_LAST_FRAME_TO_VIDEO,
        reason="current persisted strategy",
        required_capabilities=required or ["first_frame", "last_frame", "first_last_frame"],
    )


def frame(artifact_id: str, sha256: str) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        artifact_type=ArtifactType.FRAME,
        uri=f"artifact://{artifact_id}/v1",
        metadata={
            "mime_type": "image/png",
            "width": 1024,
            "height": 576,
            "media": {
                "schema_version": "1.0.0",
                "modality": "image",
                "purpose": "first_frame" if artifact_id.endswith("first") else "last_frame",
                "dimensions": {
                    "schema_version": "1.0.0",
                    "width": 1024,
                    "height": 576,
                    "aspect_ratio": "16:9",
                },
                "encoding": {
                    "schema_version": "1.0.0",
                    "mime_type": "image/png",
                    "format": "png",
                    "codec": "png",
                },
            },
        },
        provenance=Provenance(parameters={"sha256": sha256}),
    )


def current_request(**updates) -> VideoGenerationRequest:
    first = MediaReference(
        reference_type=ReferenceType.FIRST_FRAME,
        artifact_id="frame_SCENE_01-SHOT-01_first",
        shot_id="SCENE_01-SHOT-01",
    )
    last = MediaReference(
        reference_type=ReferenceType.LAST_FRAME,
        artifact_id="frame_SCENE_01-SHOT-01_last",
        shot_id="SCENE_01-SHOT-01",
    )
    request = VideoGenerationRequest(
        request_id="volatile-request-a",
        created_at=datetime(2026, 9, 7, tzinfo=UTC),
        job_id="generate:SCENE_01-SHOT-01:v1:adaptation1",
        project_id="project_3effeb45f3844bf89c2c96e83770114b",
        scene_id="SCENE_01",
        shot_id="SCENE_01-SHOT-01",
        output_artifact_id="video_SCENE_01-SHOT-01",
        prompt_package=PromptPackage(
            prompt_package_id="volatile-prompt-a",
            compiler_id="generic-video",
            compiler_version="1.0.0",
            positive_prompt="Canonical Scene 01 prompt",
            negative_prompt="flicker",
            source_shot_id="SCENE_01-SHOT-01",
        ),
        references=[first, last],
        first_frame=first,
        last_frame=last,
        mode=VideoGenerationMode.FIRST_LAST_FRAME_TO_VIDEO,
        duration_seconds=15,
        fps=24,
        width=1920,
        height=1080,
        aspect_ratio="16:9",
        seed=None,
        camera_motion=CameraMotionSpec(
            motion_type="static", direction="none", speed="None"
        ),
    )
    return request.model_copy(update=updates)


def current_frames() -> list[Artifact]:
    return [
        frame(
            "frame_SCENE_01-SHOT-01_first",
            "96357d76d811d764a0e40f6af6eb2923c7dbe6d55c3510b9da2f5d466cef3f06",
        ),
        frame(
            "frame_SCENE_01-SHOT-01_last",
            "a680c563d03eb4012caa3fed9d30ca2ba87ef7b02ade0af52a04bac4711d428b",
        ),
    ]


def run(request: VideoGenerationRequest):
    _, template, manifest = ComfyUIWorkflowRegistry().resolve("minimax_h3_fl2va")
    return VideoGenerationPreflight().prepare(
        request=request,
        strategy=strategy(),
        capabilities=capabilities(),
        template=template,
        binding_manifest=manifest,
        input_artifacts=current_frames(),
    )


def test_current_delivery_request_adapts_to_existing_flux_canvas_and_stable_seed() -> None:
    original = current_request()
    result = run(original)

    issues = [(item.code, item.field_path) for item in result.initial_issues]
    assert ("unsupported_width", "width") in issues
    assert ("delivery_generation_canvas_mismatch", "width,height") in issues
    assert ("missing_required_seed", "seed") in issues
    assert issues.count(("camera_noop_token", "camera_motion.direction")) == 1
    assert issues.count(("camera_noop_token", "camera_motion.speed")) == 1
    assert result.compatible and result.remaining_issues == []
    assert (original.width, original.height, original.seed) == (1920, 1080, None)
    assert result.requested_delivery_dimensions.model_dump(exclude={"schema_version"}) == {
        "width": 1920,
        "height": 1080,
        "aspect_ratio": "16:9",
    }
    assert (
        result.effective_generation_dimensions.width,
        result.effective_generation_dimensions.height,
    ) == (1024, 576)
    assert (result.effective_request.width, result.effective_request.height) == (1024, 576)
    assert result.effective_request.seed is not None
    assert result.effective_request.camera_motion.direction is None
    assert result.effective_request.camera_motion.speed is None
    assert result.effective_request.camera_motion.path == []
    assert result.effective_request.first_frame.artifact_id == original.first_frame.artifact_id
    assert result.effective_request.last_frame.artifact_id == original.last_frame.artifact_id
    assert result.effective_request.first_frame.version == 1
    assert result.effective_request.last_frame.version == 1

    equivalent = current_request(
        request_id="volatile-request-b",
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
        prompt_package=original.prompt_package.model_copy(
            update={"prompt_package_id": "volatile-prompt-b"}
        ),
    )
    assert run(equivalent).effective_request.seed == result.effective_request.seed


def test_real_unsupported_motion_fps_and_duration_are_reported_together() -> None:
    request = current_request(
        width=1024,
        height=576,
        seed=7,
        fps=30,
        duration_seconds=16,
        camera_motion=CameraMotionSpec(motion_type="pan", direction="left", speed="slow"),
    )

    result = run(request)

    codes = {item.code for item in result.remaining_issues}
    assert {"unsupported_fps", "unsupported_duration", "unsupported_camera_motion"} <= codes
    assert not result.compatible


def test_nonstatic_motion_is_compatible_only_when_manifest_and_prompt_encode_it() -> None:
    camera = "medium; low; 35mm; handheld; direction=shaky; speed=fast"
    request = current_request(
        width=1024,
        height=576,
        seed=7,
        prompt_package=PromptPackage(
            compiler_id="generic-video",
            compiler_version="1.0.0",
            positive_prompt=f"[camera] {camera}",
            sections=[PromptSection(name="camera", content=camera)],
            source_shot_id="SCENE_02-SHOT-01",
        ),
        camera_motion=CameraMotionSpec(
            motion_type="handheld", direction="shaky", speed="fast"
        ),
    )

    result = run(request)

    assert result.compatible
    assert "unsupported_camera_motion" not in {
        item.code for item in result.remaining_issues
    }

    for motion in (
        CameraMotionSpec(motion_type="pan", direction="left", speed="slow"),
        CameraMotionSpec(
            motion_type="handheld",
            direction="shaky",
            speed="fast",
            path=["start", "end"],
        ),
    ):
        mismatched = run(request.model_copy(update={"camera_motion": motion}))
        assert "unsupported_camera_motion" in {
            item.code for item in mismatched.remaining_issues
        }


def test_preflight_aggregates_independent_hard_failures_in_one_result() -> None:
    request = current_request(
        first_frame=None,
        last_frame=None,
        references=[],
        fps=30,
        duration_seconds=16,
        provider_parameters={"undeclared": 1},
        required_capabilities=[MediaCapabilityRequirement(capability="camera_control")],
        temporal_control=TemporalControl(preserve_identity=False),
        camera_motion=CameraMotionSpec(motion_type="pan", direction="left"),
    )
    _, template, manifest = ComfyUIWorkflowRegistry().resolve("minimax_h3_fl2va")

    result = VideoGenerationPreflight().prepare(
        request=request,
        strategy=strategy(required=["camera_control"]),
        capabilities=capabilities(),
        template=template,
        binding_manifest=manifest,
        input_artifacts=[],
    )

    codes = {item.code for item in result.initial_issues}
    assert {
        "unsupported_width",
        "unsupported_fps",
        "unsupported_duration",
        "missing_required_seed",
        "unsupported_camera_motion",
        "unsupported_temporal_control",
        "unknown_provider_parameter",
        "missing_first_frame",
        "missing_last_frame",
        "capability_mismatch",
    } <= codes
    assert not result.compatible


def test_local_preflight_failure_gets_a_distinct_adaptation_job(tmp_path) -> None:
    production = MockMovieProduction(tmp_path / "project")
    original_id = "generate:SCENE_01-SHOT-01:v1"
    original = GenerationJob(
        job_id=original_id,
        project_id="project",
        scene_id="SCENE_01",
        shot_id="SCENE_01-SHOT-01",
        node_id="shot_production",
        task="video",
        provider_id="comfyui-video",
        strategy_type="first_last_frame_to_video",
        status=JobStatus.FAILED,
        idempotency_key=original_id,
        failure_reason="media_provider_unsupported_capability",
    )
    production._restored_jobs[original_id] = original

    job_id, history = production._video_job_identity("SCENE_01-SHOT-01", 1)

    assert job_id == "generate:SCENE_01-SHOT-01:v1:adaptation1"
    assert history == [original_id]
    assert production._restored_jobs[original_id] == original


def test_remote_video_replay_requires_matching_explicit_authorization(tmp_path) -> None:
    production = MockMovieProduction(tmp_path / "project")
    original_id = "generate:SCENE_02-SHOT-01:v1:adaptation1"
    prompt_id = "309265ce-389d-471f-9240-5214d142d936"
    original = GenerationJob(
        job_id=original_id,
        project_id="project",
        scene_id="SCENE_02",
        shot_id="SCENE_02-SHOT-01",
        node_id="shot_production",
        task="video",
        provider_id="comfyui-video",
        strategy_type="first_last_frame_to_video",
        status=JobStatus.FAILED,
        remote_status=JobStatus.RUNNING,
        idempotency_key=original_id,
        failure_reason="media_provider_unavailable",
    )
    base_id = "generate:SCENE_02-SHOT-01:v1"
    production._restored_jobs[base_id] = GenerationJob(
        job_id=base_id,
        project_id="project",
        scene_id="SCENE_02",
        shot_id="SCENE_02-SHOT-01",
        node_id="shot_production",
        task="video",
        status=JobStatus.FAILED,
        idempotency_key=base_id,
        failure_reason="media_provider_unsupported_capability",
    )
    production._restored_jobs[original_id] = original
    production._emit(
        EventType.MEDIA_JOB_PROGRESS,
        original.project_id,
        {"provider_execution_graph": {"remote_prompt_id": prompt_id}},
        node_id=original.node_id,
        job_id=original.job_id,
    )

    try:
        production._video_job_identity("SCENE_02-SHOT-01", 1)
    except RuntimeError as error:
        assert "explicit remote recovery decision" in str(error)
    else:
        raise AssertionError("remote job replay must be blocked by default")

    try:
        production.authorize_video_replay(original_id, "wrong-prompt", "operator check")
    except ValueError as error:
        assert "does not match" in str(error)
    else:
        raise AssertionError("mismatched remote prompt must not authorize replay")

    production.authorize_video_replay(original_id, prompt_id, "history and output missing")
    job_id, history = production._video_job_identity("SCENE_02-SHOT-01", 1)

    assert job_id == "generate:SCENE_02-SHOT-01:v1:adaptation2"
    assert history == [
        "generate:SCENE_02-SHOT-01:v1",
        original_id,
    ]
    assert production.event_bus.events()[-1].event_type == EventType.MEDIA_JOB_REPLAY_AUTHORIZED


def test_resume_reuses_a_successful_partial_shot_output(tmp_path) -> None:
    production = MockMovieProduction(tmp_path / "project")
    job_id = "generate:SCENE_01-SHOT-01:v1:adaptation1"
    video_id = "video_SCENE_01-SHOT-01"
    completed = GenerationJob(
        job_id=job_id,
        project_id="project",
        scene_id="SCENE_01",
        shot_id="SCENE_01-SHOT-01",
        node_id="shot_production",
        task="video",
        provider_id="comfyui-video",
        strategy_type="first_last_frame_to_video",
        status=JobStatus.SUCCEEDED,
        idempotency_key=job_id,
        output_artifact_ids=[video_id, f"{video_id}_generated_native_audio"],
    )
    artifact = Artifact(
        artifact_id=video_id,
        artifact_type=ArtifactType.VIDEO,
        uri=f"artifact://{video_id}/v1",
        source_job_id=job_id,
        provenance=Provenance(provider_id="comfyui-video"),
    )
    production.artifact_store.register(artifact)
    production._restored_jobs[job_id] = completed

    reused = production._reusable_video_generation("SCENE_01-SHOT-01")

    assert reused == (completed, artifact)
