"""Provider-neutral media, inspection, repair, and post-production contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, PositiveFloat, model_validator

from movie_agent.domain.base import ContractModel, JSONValue, Provenance, new_id, utc_now
from movie_agent.domain.cinematic import PromptPackage, Shot
from movie_agent.domain.enums import (
    AspectRatio,
    CameraMotionType,
    IssueSeverity,
    ProviderKind,
    QualityProfile,
    ResourceClass,
    GenerationStrategyType,
)


class MediaModality(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    VISION = "vision"
    POST = "post"


class AudioPurpose(StrEnum):
    SPEECH = "speech"
    MUSIC = "music"
    SFX = "sfx"
    FOLEY = "foley"
    AMBIENCE = "ambience"
    MIX = "mix"
    GENERATED_NATIVE_AUDIO = "generated_native_audio"


class ImageGenerationMode(StrEnum):
    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_IMAGE = "image_to_image"
    REFERENCE_TO_IMAGE = "reference_to_image"
    IMAGE_EDIT = "image_edit"
    INPAINT = "inpaint"
    OUTPAINT = "outpaint"
    VARIATION = "variation"
    UPSCALE = "upscale"


class ImagePurpose(StrEnum):
    CHARACTER_PLATE = "character_plate"
    LOCATION_PLATE = "location_plate"
    PROP_PLATE = "prop_plate"
    STYLE_FRAME = "style_frame"
    STORYBOARD = "storyboard"
    FIRST_FRAME = "first_frame"
    LAST_FRAME = "last_frame"
    POSTER = "poster"
    THUMBNAIL = "thumbnail"


class VideoGenerationMode(StrEnum):
    TEXT_TO_VIDEO = "text_to_video"
    IMAGE_TO_VIDEO = "image_to_video"
    FIRST_FRAME_TO_VIDEO = "first_frame_to_video"
    FIRST_LAST_FRAME_TO_VIDEO = "first_last_frame_to_video"
    REFERENCE_TO_VIDEO = "reference_to_video"
    VIDEO_TO_VIDEO = "video_to_video"
    VIDEO_EXTEND = "video_extend"


class VideoPreflightDisposition(StrEnum):
    ADAPTABLE = "adaptable"
    HARD_UNSUPPORTED = "hard_unsupported"


class ReferenceType(StrEnum):
    CHARACTER = "character"
    LOCATION = "location"
    PROP = "prop"
    STYLE = "style"
    FIRST_FRAME = "first_frame"
    LAST_FRAME = "last_frame"
    PREVIOUS_FRAME = "previous_frame"
    SOURCE_IMAGE = "source_image"
    PREVIOUS_SHOT = "previous_shot"
    VOICE = "voice"
    AUDIO_REFERENCE = "audio_reference"


class ReferenceBindingScope(StrEnum):
    PROJECT = "project"
    CREATIVE_INPUT = "creative_input"
    ENTITY = "entity"
    SCENE = "scene"
    SHOT = "shot"
    FRAME = "frame"


class ReferencePurpose(StrEnum):
    VISUAL_STYLE = "visual_style"
    CHARACTER_IDENTITY = "character_identity"
    ENVIRONMENT = "environment"
    PROP_IDENTITY = "prop_identity"
    COMPOSITION = "composition"
    SCENE_CONCEPT = "scene_concept"
    KEY_VISUAL = "key_visual"
    SHOT_GUIDANCE = "shot_guidance"
    FIRST_FRAME = "first_frame"
    LAST_FRAME = "last_frame"


class MotionStrength(StrEnum):
    SUBTLE = "subtle"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class VisionInspectionProfile(StrEnum):
    IMAGE_QUALITY = "image_quality"
    VIDEO_QUALITY = "video_quality"
    CHARACTER_IDENTITY = "character_identity"
    SCENE_CONSISTENCY = "scene_consistency"
    PROMPT_ALIGNMENT = "prompt_alignment"
    ACTION_COMPLETION = "action_completion"
    CAMERA_MOTION = "camera_motion"
    CONTINUITY = "continuity"
    ARTIFACT_DETECTION = "artifact_detection"
    FULL_SHOT_REVIEW = "full_shot_review"


class VisionDecision(StrEnum):
    PASS = "pass"
    REPAIR = "repair"
    REGENERATE = "regenerate"
    HUMAN_REVIEW = "human_review"


class MediaIssueType(StrEnum):
    IDENTITY_DRIFT = "identity_drift"
    WARDROBE_DRIFT = "wardrobe_drift"
    SCENE_DRIFT = "scene_drift"
    PROP_DRIFT = "prop_drift"
    EXTRA_CHARACTER = "extra_character"
    MISSING_CHARACTER = "missing_character"
    ANATOMY_ARTIFACT = "anatomy_artifact"
    HAND_ARTIFACT = "hand_artifact"
    FACE_ARTIFACT = "face_artifact"
    MOTION_FAILURE = "motion_failure"
    ACTION_INCOMPLETE = "action_incomplete"
    CAMERA_MOTION_MISMATCH = "camera_motion_mismatch"
    FIRST_FRAME_MISMATCH = "first_frame_mismatch"
    LAST_FRAME_MISMATCH = "last_frame_mismatch"
    TEMPORAL_FLICKER = "temporal_flicker"
    DETAIL_LOSS = "detail_loss"
    STYLE_MISMATCH = "style_mismatch"
    LIGHTING_MISMATCH = "lighting_mismatch"
    CONTINUITY_ERROR = "continuity_error"
    AUDIO_SYNC_ERROR = "audio_sync_error"
    SPEECH_ERROR = "speech_error"
    MUSIC_MISMATCH = "music_mismatch"
    TECHNICAL_MEDIA_ERROR = "technical_media_error"


class MediaRepairActionType(StrEnum):
    REWRITE_PROMPT = "rewrite_prompt"
    STRENGTHEN_CHARACTER_REFERENCE = "strengthen_character_reference"
    STRENGTHEN_SCENE_REFERENCE = "strengthen_scene_reference"
    CHANGE_REFERENCE = "change_reference"
    REGENERATE_FIRST_FRAME = "regenerate_first_frame"
    REGENERATE_LAST_FRAME = "regenerate_last_frame"
    IMAGE_EDIT = "image_edit"
    CHANGE_GENERATION_STRATEGY = "change_generation_strategy"
    REGENERATE_VIDEO = "regenerate_video"
    EXTEND_VIDEO = "extend_video"
    SPLIT_SHOT = "split_shot"
    CHANGE_CAMERA_CONTROL = "change_camera_control"
    UPSCALE = "upscale"
    DETAIL_RESTORE = "detail_restore"
    REGENERATE_VOICE = "regenerate_voice"
    REGENERATE_MUSIC = "regenerate_music"
    REGENERATE_SFX = "regenerate_sfx"
    REMIX_AUDIO = "remix_audio"
    REQUEST_HUMAN_REVIEW = "request_human_review"


class ModelServiceStatus(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    BUSY = "busy"
    STOPPING = "stopping"
    FAILED = "failed"


class MediaDimensions(ContractModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    aspect_ratio: AspectRatio | str


class MediaDuration(ContractModel):
    seconds: PositiveFloat


class MediaEncoding(ContractModel):
    mime_type: str = Field(pattern=r"^(image|video|audio)/[A-Za-z0-9.+-]+$")
    format: str = Field(min_length=1)
    codec: str | None = None
    bitrate_kbps: int | None = Field(default=None, gt=0)


class ResourceProfile(ContractModel):
    resource_class: ResourceClass
    expected_memory_gb: float | None = Field(default=None, gt=0)
    supports_concurrency: bool = True
    requires_exclusive_runtime: bool = False


class MediaReference(ContractModel):
    reference_id: str = Field(default_factory=lambda: new_id("reference"))
    reference_type: ReferenceType
    artifact_id: str = Field(min_length=1)
    version: int | None = Field(default=None, ge=1)
    binding_scope: ReferenceBindingScope | None = None
    purpose: ReferencePurpose | None = None
    project_id: str | None = None
    entity_id: str | None = None
    scene_id: str | None = None
    shot_id: str | None = None
    binding_key: str | None = None
    selected: bool = True
    original_filename: str | None = None
    mime_type: str | None = Field(default=None, pattern=r"^image/(png|jpeg|webp)$")
    size_bytes: int | None = Field(default=None, gt=0)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)

    @property
    def artifact_uri(self) -> str:
        suffix = f"/v{self.version}" if self.version else ""
        return f"artifact://{self.artifact_id}{suffix}"


class MediaCapabilityRequirement(ContractModel):
    capability: str = Field(min_length=1)
    required: bool = True


class MediaArtifactMetadata(ContractModel):
    modality: MediaModality
    purpose: str
    dimensions: MediaDimensions | None = None
    duration: MediaDuration | None = None
    encoding: MediaEncoding
    mock: bool = False
    test_asset: bool = False
    preview_artifact_id: str | None = None
    thumbnail_artifact_id: str | None = None
    waveform: list[float] = Field(default_factory=list)


class MediaRequestBase(ContractModel):
    request_id: str = Field(default_factory=lambda: new_id("mediareq"))
    job_id: str
    project_id: str
    scene_id: str | None = None
    shot_id: str | None = None
    prompt_package: PromptPackage
    references: list[MediaReference] = Field(default_factory=list)
    quality_profile: QualityProfile = QualityProfile.STANDARD
    resource_class: ResourceClass = ResourceClass.MEDIUM
    seed: int | None = Field(default=None, ge=0)
    required_capabilities: list[MediaCapabilityRequirement] = Field(default_factory=list)
    provider_parameters: dict[str, JSONValue] = Field(default_factory=dict)
    output_artifact_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)


class ImageGenerationRequest(MediaRequestBase):
    mode: ImageGenerationMode
    purpose: ImagePurpose
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    aspect_ratio: AspectRatio | str
    source_image: MediaReference | None = None
    mask_artifact_id: str | None = None

    @property
    def input_artifact_ids(self) -> list[str]:
        return list(dict.fromkeys([item.artifact_id for item in self.references]
            + ([self.source_image.artifact_id] if self.source_image else [])))


class CameraMotionSpec(ContractModel):
    motion_type: CameraMotionType
    direction: str | None = None
    strength: MotionStrength = MotionStrength.MEDIUM
    speed: str | None = None
    path: list[str] = Field(default_factory=list)


class TemporalControl(ContractModel):
    preserve_identity: bool = True
    preserve_scene: bool = True
    action_completion_required: bool = True
    temporal_consistency: MotionStrength = MotionStrength.MEDIUM


class StartState(ContractModel):
    description: str = ""
    frame_reference: MediaReference | None = None


class EndState(ContractModel):
    description: str = ""
    frame_reference: MediaReference | None = None


class VideoGenerationRequest(MediaRequestBase):
    shot_id: str
    mode: VideoGenerationMode
    duration_seconds: PositiveFloat
    fps: PositiveFloat
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    aspect_ratio: AspectRatio | str
    first_frame: MediaReference | None = None
    last_frame: MediaReference | None = None
    previous_shot: MediaReference | None = None
    camera_motion: CameraMotionSpec
    temporal_control: TemporalControl = Field(default_factory=TemporalControl)
    start_state: StartState = Field(default_factory=StartState)
    end_state: EndState = Field(default_factory=EndState)


class VideoPreflightIssue(ContractModel):
    code: str = Field(min_length=1)
    field_path: str = Field(min_length=1)
    request_value: JSONValue | None = None
    requirement: str = Field(min_length=1)
    disposition: VideoPreflightDisposition
    adaptation_owner: str = Field(min_length=1)
    resolved_value: JSONValue | None = None


class VideoPreflightResult(ContractModel):
    original_request: VideoGenerationRequest
    effective_request: VideoGenerationRequest
    initial_issues: list[VideoPreflightIssue] = Field(default_factory=list)
    remaining_issues: list[VideoPreflightIssue] = Field(default_factory=list)
    compatible: bool
    requested_delivery_dimensions: MediaDimensions
    effective_generation_dimensions: MediaDimensions
    adaptation_reason: list[str] = Field(default_factory=list)
    input_artifacts: list[dict[str, JSONValue]] = Field(default_factory=list)


class AudioRequestBase(MediaRequestBase):
    purpose: AudioPurpose
    duration_target_seconds: PositiveFloat | None = None


class SpeechGenerationRequest(AudioRequestBase):
    purpose: AudioPurpose = AudioPurpose.SPEECH
    text: str = Field(min_length=1)
    character_id: str | None = None
    voice_profile: str = ""
    language: str = "en"
    emotion: str = ""
    pace: str = "medium"
    prosody: list[str] = Field(default_factory=list)
    reference_voice: MediaReference | None = None


class EmotionPoint(ContractModel):
    at_seconds: float = Field(ge=0)
    emotion: str
    intensity: float = Field(default=0.5, ge=0, le=1)


class MusicGenerationRequest(AudioRequestBase):
    purpose: AudioPurpose = AudioPurpose.MUSIC
    mood: str
    genre: list[str] = Field(default_factory=list)
    emotion_curve: list[EmotionPoint] = Field(default_factory=list)
    tempo_bpm: PositiveFloat | None = None
    energy: float = Field(default=0.5, ge=0, le=1)
    instrument_preferences: list[str] = Field(default_factory=list)
    loop: bool = False
    extend_artifact_id: str | None = None


class SpatialPlacement(ContractModel):
    pan: float = Field(default=0, ge=-1, le=1)
    distance: str = "medium"
    environment: str = ""


class SoundEffectGenerationRequest(AudioRequestBase):
    purpose: AudioPurpose = AudioPurpose.SFX
    event_description: str = Field(min_length=1)
    source_video_artifact_id: str | None = None
    timing_seconds: float = Field(default=0, ge=0)
    spatial_placement: SpatialPlacement = Field(default_factory=SpatialPlacement)
    intensity: float = Field(default=0.5, ge=0, le=1)


class FoleyGenerationRequest(AudioRequestBase):
    purpose: AudioPurpose = AudioPurpose.FOLEY
    event_description: str = Field(min_length=1)
    source_video_artifact_id: str
    timing_seconds: float = Field(default=0, ge=0)
    spatial_placement: SpatialPlacement = Field(default_factory=SpatialPlacement)
    intensity: float = Field(default=0.5, ge=0, le=1)


class MediaResultBase(ContractModel):
    result_id: str = Field(default_factory=lambda: new_id("mediaresult"))
    request_id: str
    artifact_ids: list[str] = Field(default_factory=list)
    primary_artifact_id: str
    provider_id: str
    model_service_id: str | None = None
    provider_metadata: dict[str, JSONValue] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=Provenance)
    completed_at: datetime = Field(default_factory=utc_now)


class ImageGenerationResult(MediaResultBase):
    seed: int | None = None
    dimensions: MediaDimensions
    encoding: MediaEncoding


class GeneratedAudioOutput(ContractModel):
    """A normal audio Artifact emitted by the same job as a generated video."""

    artifact_id: str = Field(min_length=1)
    purpose: AudioPurpose = AudioPurpose.MIX
    duration_seconds: PositiveFloat | None = None
    sample_rate: int | None = Field(default=None, gt=0)
    channels: int | None = Field(default=None, gt=0)
    encoding: MediaEncoding


class VideoGenerationResult(MediaResultBase):
    duration_seconds: PositiveFloat
    fps: PositiveFloat
    dimensions: MediaDimensions
    frame_count: int = Field(gt=0)
    codec: str
    encoding: MediaEncoding
    native_audio_outputs: list[GeneratedAudioOutput] = Field(default_factory=list)


class AudioGenerationResult(MediaResultBase):
    duration_seconds: PositiveFloat
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    format: str
    loudness_lufs: float | None = None
    purpose: AudioPurpose
    encoding: MediaEncoding


class TimeRange(ContractModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_order(self) -> "TimeRange":
        if self.end_seconds < self.start_seconds:
            raise ValueError("time range end must not precede start")
        return self


class FrameReference(ContractModel):
    artifact_id: str
    frame_number: int | None = Field(default=None, ge=0)
    timestamp_seconds: float | None = Field(default=None, ge=0)


class VisionScore(ContractModel):
    profile: VisionInspectionProfile
    score: float = Field(ge=0, le=1)


class MediaIssue(ContractModel):
    issue_id: str = Field(default_factory=lambda: new_id("mediaissue"))
    issue_type: MediaIssueType
    severity: IssueSeverity
    message: str
    evidence: list[str] = Field(default_factory=list)
    time_ranges: list[TimeRange] = Field(default_factory=list)
    frame_references: list[FrameReference] = Field(default_factory=list)
    suggested_action: MediaRepairActionType | None = None


class VisionInspectionRequest(ContractModel):
    request_id: str = Field(default_factory=lambda: new_id("visionreq"))
    job_id: str
    project_id: str
    scene_id: str | None = None
    shot_id: str | None = None
    image_artifact_id: str | None = None
    video_artifact_id: str | None = None
    reference_assets: list[MediaReference] = Field(default_factory=list)
    expected_shot: Shot | None = None
    expected_requirements: list[str] = Field(default_factory=list)
    profiles: list[VisionInspectionProfile]
    output_artifact_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_target(self) -> "VisionInspectionRequest":
        if bool(self.image_artifact_id) == bool(self.video_artifact_id):
            raise ValueError("inspection requires exactly one image or video artifact")
        return self


class VisionInspectionResult(ContractModel):
    result_id: str = Field(default_factory=lambda: new_id("visionresult"))
    request_id: str
    target_artifact_id: str
    scores: list[VisionScore]
    issues: list[MediaIssue] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    decision: VisionDecision
    summary: str = ""
    provider_id: str
    provider_metadata: dict[str, JSONValue] = Field(default_factory=dict)


class MediaRepairAction(ContractModel):
    action_id: str = Field(default_factory=lambda: new_id("mediarepairaction"))
    action_type: MediaRepairActionType
    issue_ids: list[str] = Field(default_factory=list)
    target_artifact_id: str | None = None
    target_shot_id: str | None = None
    rationale: str
    consumes_retry: bool = True


class MediaRepairPlan(ContractModel):
    repair_plan_id: str = Field(default_factory=lambda: new_id("mediarepair"))
    inspection_result_id: str
    actions: list[MediaRepairAction] = Field(default_factory=list)
    retry_budget: int = Field(ge=0)
    retry_count: int = Field(default=0, ge=0)
    exhausted: bool = False
    requires_human: bool = False

    @model_validator(mode="after")
    def validate_budget(self) -> "MediaRepairPlan":
        if self.retry_count > self.retry_budget:
            raise ValueError("media repair retry_count cannot exceed retry_budget")
        return self


class FramePlan(ContractModel):
    shot_id: str
    first_frame_request: ImageGenerationRequest
    last_frame_request: ImageGenerationRequest
    previous_last_frame_reference: MediaReference | None = None
    requested_dimensions: MediaDimensions | None = None


class AudioCue(ContractModel):
    cue_id: str = Field(default_factory=lambda: new_id("cue"))
    start_time_seconds: float = Field(ge=0)
    duration_seconds: PositiveFloat
    cue_type: AudioPurpose
    artifact_id: str
    gain_db: float = 0
    fade_in_seconds: float = Field(default=0, ge=0)
    fade_out_seconds: float = Field(default=0, ge=0)
    scene_id: str | None = None
    shot_id: str | None = None


class TimelineClip(ContractModel):
    clip_id: str = Field(default_factory=lambda: new_id("clip"))
    artifact_id: str
    start_time_seconds: float = Field(ge=0)
    source_in_seconds: float = Field(default=0, ge=0)
    duration_seconds: PositiveFloat
    trim_end_seconds: float = Field(default=0, ge=0)
    transition: str | None = None
    shot_id: str | None = None


class VideoTrack(ContractModel):
    track_id: str = Field(default_factory=lambda: new_id("videotrack"))
    clips: list[TimelineClip] = Field(default_factory=list)


class AudioTrack(ContractModel):
    track_id: str = Field(default_factory=lambda: new_id("audiotrack"))
    cues: list[AudioCue] = Field(default_factory=list)


class SubtitleCue(ContractModel):
    start_time_seconds: float = Field(ge=0)
    end_time_seconds: float = Field(ge=0)
    text: str


class SubtitleTrack(ContractModel):
    track_id: str = Field(default_factory=lambda: new_id("subtitletrack"))
    language: str
    cues: list[SubtitleCue] = Field(default_factory=list)


class Timeline(ContractModel):
    timeline_id: str = Field(default_factory=lambda: new_id("timeline"))
    project_id: str
    duration_seconds: PositiveFloat
    video_tracks: list[VideoTrack] = Field(default_factory=list)
    audio_tracks: list[AudioTrack] = Field(default_factory=list)
    subtitle_tracks: list[SubtitleTrack] = Field(default_factory=list)


class PostProductionRequest(ContractModel):
    request_id: str = Field(default_factory=lambda: new_id("postreq"))
    job_id: str
    project_id: str
    timeline: Timeline
    output_artifact_id: str
    output_format: str = "mp4"
    video_codec: str = "h264"
    audio_codec: str = "aac"
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: PositiveFloat
    resource_class: ResourceClass = ResourceClass.MEDIUM


class PostProductionResult(MediaResultBase):
    duration_seconds: PositiveFloat
    dimensions: MediaDimensions
    encoding: MediaEncoding


class ImageCapabilities(ContractModel):
    text_to_image: bool = False
    image_to_image: bool = False
    image_edit: bool = False
    inpaint: bool = False
    outpaint: bool = False
    upscale: bool = False
    multi_reference: bool = False
    character_reference: bool = False
    max_width: int | None = Field(default=None, gt=0)
    max_height: int | None = Field(default=None, gt=0)
    min_dimension: int = Field(default=1, gt=0)
    dimension_multiple: int = Field(default=1, gt=0)


class VideoCapabilities(ContractModel):
    text_to_video: bool = False
    image_to_video: bool = False
    video_to_video: bool = False
    video_extend: bool = False
    first_frame: bool = False
    last_frame: bool = False
    first_last_frame: bool = False
    multi_reference: bool = False
    camera_control: bool = False
    audio_generation: bool = False
    max_duration_seconds: PositiveFloat | None = None
    max_width: int | None = Field(default=None, gt=0)
    max_height: int | None = Field(default=None, gt=0)
    supported_fps: list[PositiveFloat] = Field(default_factory=list)


class VisionCapabilities(ContractModel):
    image: bool = False
    video: bool = False
    multi_image: bool = False
    temporal_reasoning: bool = False


class AudioCapabilities(ContractModel):
    tts: bool = False
    voice_clone: bool = False
    music: bool = False
    foley: bool = False
    sfx: bool = False
    ambience: bool = False
    audio_edit: bool = False


class ProviderCapabilities(ContractModel):
    provider_id: str
    kind: ProviderKind
    modalities: list[MediaModality]
    tasks: list[str] = Field(default_factory=list)
    image: ImageCapabilities | None = None
    video: VideoCapabilities | None = None
    vision: VisionCapabilities | None = None
    audio: AudioCapabilities | None = None
    resource_profiles: list[ResourceProfile] = Field(default_factory=list)
    quality_profiles: list[QualityProfile] = Field(default_factory=list)
    supports_cancellation: bool = True
    model_service_id: str | None = None


class MediaRoutingRequest(ContractModel):
    modality: MediaModality
    task: str
    required_capabilities: list[str] = Field(default_factory=list)
    quality_profile: QualityProfile = QualityProfile.STANDARD
    resource_class: ResourceClass = ResourceClass.MEDIUM


class MediaProviderSelection(ContractModel):
    selection_id: str = Field(default_factory=lambda: new_id("mediaselection"))
    provider_id: str
    capabilities: ProviderCapabilities
    reason: str


class MediaGenerationStrategy(ContractModel):
    strategy_type: GenerationStrategyType
    reason: str
    required_capabilities: list[str] = Field(default_factory=list)
    input_artifact_ids: list[str] = Field(default_factory=list)
    fallback_types: list[GenerationStrategyType] = Field(default_factory=list)
    split_shot: bool = False
    preparatory_image_edit: bool = False
