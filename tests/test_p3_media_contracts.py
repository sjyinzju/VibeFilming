"""P3 media contracts stay typed, serializable, and provider-neutral."""

from __future__ import annotations

from movie_agent.domain import GenerationStrategyType, ResourceClass
from movie_agent.media import (
    AudioCue,
    AudioPurpose,
    AudioTrack,
    CameraMotionSpec,
    FrameReference,
    GenericAudioPromptCompiler,
    GenericImagePromptCompiler,
    GenericVideoPromptCompiler,
    ImageGenerationMode,
    ImageGenerationRequest,
    ImagePurpose,
    MediaFramePlanner,
    MediaGenerationStrategy,
    MediaIssue,
    MediaIssueType,
    MediaReference,
    MediaRepairActionType,
    MusicGenerationRequest,
    ReferenceType,
    SoundEffectGenerationRequest,
    SpeechGenerationRequest,
    FoleyGenerationRequest,
    TimeRange,
    Timeline,
    TimelineClip,
    VideoGenerationMode,
    VideoGenerationRequest,
    VideoTrack,
    VisionDecision,
    VisionInspectionProfile,
    VisionInspectionRequest,
    VisionInspectionResult,
    VisionScore,
)
from tests.test_contracts import sample_shot


def test_image_video_audio_and_vision_contract_round_trips() -> None:
    shot = sample_shot()
    frame_plan, _ = MediaFramePlanner().plan(
        "project_1", shot, width=1920, height=1080, aspect_ratio="16:9"
    )
    image = frame_plan.first_frame_request
    assert ImageGenerationRequest.model_validate_json(image.model_dump_json()) == image
    assert image.mode == ImageGenerationMode.TEXT_TO_IMAGE
    assert image.purpose == ImagePurpose.FIRST_FRAME

    strategy = MediaGenerationStrategy(
        strategy_type=GenerationStrategyType.FIRST_LAST_FRAME_TO_VIDEO,
        reason="two anchors", required_capabilities=["first_frame", "last_frame"],
    )
    references = [
        MediaReference(reference_type=ReferenceType.FIRST_FRAME, artifact_id="first"),
        MediaReference(reference_type=ReferenceType.LAST_FRAME, artifact_id="last"),
    ]
    video = VideoGenerationRequest(
        job_id="video-job", project_id="project_1", shot_id=shot.shot_id,
        scene_id=shot.scene_id,
        prompt_package=GenericVideoPromptCompiler().compile(shot, strategy, references),
        references=references, output_artifact_id="video_1",
        mode=VideoGenerationMode.FIRST_LAST_FRAME_TO_VIDEO,
        duration_seconds=5, fps=24, width=1920, height=1080, aspect_ratio="16:9",
        first_frame=references[0], last_frame=references[1],
        camera_motion=CameraMotionSpec(motion_type="static"),
    )
    assert VideoGenerationRequest.model_validate_json(video.model_dump_json()) == video

    audio_compiler = GenericAudioPromptCompiler()
    speech = SpeechGenerationRequest(
        job_id="speech", project_id="project_1",
        prompt_package=audio_compiler.compile(AudioPurpose.SPEECH, "Line", []),
        output_artifact_id="speech_1", text="Line", duration_target_seconds=1,
    )
    music = MusicGenerationRequest(
        job_id="music", project_id="project_1",
        prompt_package=audio_compiler.compile(AudioPurpose.MUSIC, "Tense score", []),
        output_artifact_id="music_1", mood="tense", duration_target_seconds=5,
    )
    sfx = SoundEffectGenerationRequest(
        job_id="sfx", project_id="project_1",
        prompt_package=audio_compiler.compile(AudioPurpose.SFX, "Door", []),
        output_artifact_id="sfx_1", event_description="Door", duration_target_seconds=1,
    )
    foley = FoleyGenerationRequest(
        job_id="foley", project_id="project_1",
        prompt_package=audio_compiler.compile(AudioPurpose.FOLEY, "Steps", []),
        output_artifact_id="foley_1", event_description="Steps",
        source_video_artifact_id="video_1", duration_target_seconds=5,
    )
    for contract in (speech, music, sfx, foley):
        assert type(contract).model_validate_json(contract.model_dump_json()) == contract

    inspection = VisionInspectionRequest(
        job_id="vision", project_id="project_1", shot_id=shot.shot_id,
        video_artifact_id="video_1", expected_shot=shot,
        profiles=[VisionInspectionProfile.FULL_SHOT_REVIEW],
        output_artifact_id="inspection_1",
    )
    result = VisionInspectionResult(
        request_id=inspection.request_id, target_artifact_id="video_1",
        scores=[VisionScore(profile=VisionInspectionProfile.FULL_SHOT_REVIEW, score=0.5)],
        issues=[MediaIssue(
            issue_type=MediaIssueType.TEMPORAL_FLICKER, severity="major",
            message="Flicker", time_ranges=[TimeRange(start_seconds=1, end_seconds=2)],
            frame_references=[FrameReference(artifact_id="video_1", frame_number=24)],
            suggested_action=MediaRepairActionType.REGENERATE_VIDEO,
        )],
        decision=VisionDecision.REPAIR, provider_id="mock-vision",
    )
    assert VisionInspectionResult.model_validate_json(result.model_dump_json()) == result


def test_generic_compilers_and_timeline_are_provider_neutral() -> None:
    shot = sample_shot()
    reference = MediaReference(reference_type=ReferenceType.CHARACTER, artifact_id="plate_hero")
    image = GenericImagePromptCompiler().compile(shot, ImagePurpose.CHARACTER_PLATE, [reference])
    assert image.compiler_id == "generic-image" and "plate_hero" in image.positive_prompt
    assert "flux" not in image.model_dump_json().lower()

    timeline = Timeline(
        project_id="project_1", duration_seconds=5,
        video_tracks=[VideoTrack(clips=[TimelineClip(
            artifact_id="video_1", start_time_seconds=0, duration_seconds=5,
        )])],
        audio_tracks=[AudioTrack(cues=[AudioCue(
            start_time_seconds=0, duration_seconds=5,
            cue_type=AudioPurpose.MUSIC, artifact_id="music_1",
        )])],
    )
    assert Timeline.model_validate_json(timeline.model_dump_json()) == timeline
    assert timeline.video_tracks[0].clips[0].artifact_id == "video_1"


def test_frame_plan_carries_previous_last_frame_by_artifact_identity() -> None:
    previous = sample_shot().model_copy(update={"shot_id": "shot_previous"}, deep=True)
    current = sample_shot().model_copy(update={
        "shot_id": "shot_current", "previous_shot_id": previous.shot_id,
        "continuity_chain_id": "chain_1",
    }, deep=True)
    previous.continuity_chain_id = "chain_1"
    plan, anchors = MediaFramePlanner().plan(
        "project_1", current, width=1280, height=720, aspect_ratio="16:9",
        previous_shot=previous, previous_last_frame_artifact_id="frame_previous_last",
    )
    assert plan.previous_last_frame_reference.artifact_id == "frame_previous_last"
    assert plan.first_frame_request.references[0].reference_type == ReferenceType.PREVIOUS_FRAME
    assert anchors.first_frame.source_artifact_id == "frame_previous_last"
