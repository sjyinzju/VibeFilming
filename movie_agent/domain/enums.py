"""Stable vocabulary shared across the cinematic and execution contracts."""

from enum import StrEnum


class AspectRatio(StrEnum):
    RATIO_16_9 = "16:9"
    RATIO_9_16 = "9:16"
    RATIO_1_1 = "1:1"
    RATIO_2_39_1 = "2.39:1"


class Pacing(StrEnum):
    SLOW = "slow"
    MEASURED = "measured"
    MODERATE = "moderate"
    FAST = "fast"
    VARIABLE = "variable"


class CreativeFreedomLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class QualityProfile(StrEnum):
    DRAFT = "draft"
    STANDARD = "standard"
    HIGH = "high"
    FINAL = "final"


class ShotSize(StrEnum):
    EXTREME_WIDE = "extreme_wide"
    WIDE = "wide"
    FULL = "full"
    MEDIUM = "medium"
    CLOSE_UP = "close_up"
    EXTREME_CLOSE_UP = "extreme_close_up"
    INSERT = "insert"


class CameraAngle(StrEnum):
    EYE_LEVEL = "eye_level"
    HIGH = "high"
    LOW = "low"
    DUTCH = "dutch"
    OVERHEAD = "overhead"
    POV = "pov"


class CameraMotionType(StrEnum):
    STATIC = "static"
    PAN = "pan"
    TILT = "tilt"
    DOLLY = "dolly"
    TRACK = "track"
    CRANE = "crane"
    HANDHELD = "handheld"
    ZOOM = "zoom"
    ORBIT = "orbit"


class Orientation(StrEnum):
    FRONT = "front"
    BACK = "back"
    LEFT = "left"
    RIGHT = "right"
    THREE_QUARTER_LEFT = "three_quarter_left"
    THREE_QUARTER_RIGHT = "three_quarter_right"


class PropCondition(StrEnum):
    INTACT = "intact"
    DAMAGED = "damaged"
    BROKEN = "broken"
    MISSING = "missing"


class AnchorKind(StrEnum):
    NONE = "none"
    GENERATED = "generated"
    PREVIOUS_SHOT_LAST_FRAME = "previous_shot_last_frame"
    EXTERNAL_REFERENCE = "external_reference"


class GenerationStrategyType(StrEnum):
    STRUCTURED_TEXT = "structured_text"
    TEXT_TO_VIDEO = "text_to_video"
    IMAGE_TO_VIDEO = "image_to_video"
    FIRST_FRAME_TO_VIDEO = "first_frame_to_video"
    FIRST_LAST_FRAME_TO_VIDEO = "first_last_frame_to_video"
    REFERENCE_TO_VIDEO = "reference_to_video"
    IMAGE_EDIT_THEN_VIDEO = "image_edit_then_video"
    VIDEO_EXTEND = "video_extend"
    VIDEO_TO_VIDEO = "video_to_video"
    SPLIT_SHOT = "split_shot"
    STATIC_PLUS_POST_CAMERA = "static_plus_post_camera"


class ArtifactType(StrEnum):
    TEXT = "text"
    STORY_BIBLE = "story_bible"
    VISUAL_BIBLE = "visual_bible"
    SCREENPLAY = "screenplay"
    SHOT_PLAN = "shot_plan"
    IMAGE = "image"
    FRAME = "frame"
    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLE = "subtitle"
    TIMELINE = "timeline"
    FINAL_FILM = "final_film"
    CHECKPOINT = "checkpoint"


class JobStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    BLOCKED = "blocked"
    WAITING_RESOURCE = "waiting_resource"
    PREPARING = "preparing"
    PREPARING_MODEL = "preparing_model"
    RUNNING = "running"
    UPLOADING = "uploading"
    EVALUATING = "evaluating"
    REPAIRING = "repairing"
    WAITING_HUMAN = "waiting_human"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResourceClass(StrEnum):
    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"
    EXCLUSIVE = "exclusive"


class WorkflowNodeStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class WorkflowEdgeType(StrEnum):
    DEPENDENCY = "dependency"
    CONDITION = "condition"
    SUCCESS = "success"
    FAILURE = "failure"
    REPAIR = "repair"
    HUMAN_GATE = "human_gate"


class EventType(StrEnum):
    PROVIDER_REQUEST_STARTED = "provider_request_started"
    PROVIDER_REQUEST_COMPLETED = "provider_request_completed"
    ROLE_OUTPUT_RECEIVED = "role_output_received"
    ROLE_OUTPUT_VALIDATION_FAILED = "role_output_validation_failed"
    ROLE_OUTPUT_VALIDATED = "role_output_validated"
    CHECKPOINT_CREATED = "checkpoint_created"
    PROJECT_CREATED = "project_created"
    NODE_CREATED = "node_created"
    NODE_STARTED = "node_started"
    NODE_PROGRESS = "node_progress"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED = "node_failed"
    EDGE_CREATED = "edge_created"
    EDGE_ACTIVATED = "edge_activated"
    JOB_CREATED = "job_created"
    JOB_STARTED = "job_started"
    JOB_PROGRESS = "job_progress"
    JOB_COMPLETED = "job_completed"
    JOB_FAILED = "job_failed"
    JOB_CANCELLED = "job_cancelled"
    ARTIFACT_CREATED = "artifact_created"
    ARTIFACT_SELECTED = "artifact_selected"
    EVALUATION_COMPLETED = "evaluation_completed"
    REPAIR_STARTED = "repair_started"
    REPAIR_COMPLETED = "repair_completed"
    HUMAN_REVIEW_REQUESTED = "human_review_requested"
    HUMAN_REVIEW_RESOLVED = "human_review_resolved"
    WORKFLOW_COMPLETED = "workflow_completed"
    MEDIA_JOB_CREATED = "media_job_created"
    MEDIA_JOB_STARTED = "media_job_started"
    MEDIA_JOB_PROGRESS = "media_job_progress"
    MEDIA_JOB_COMPLETED = "media_job_completed"
    MEDIA_JOB_FAILED = "media_job_failed"
    MEDIA_JOB_CANCELLED = "media_job_cancelled"
    MEDIA_JOB_REPLAY_AUTHORIZED = "media_job_replay_authorized"
    MEDIA_EVALUATION_STARTED = "media_evaluation_started"
    MEDIA_EVALUATION_COMPLETED = "media_evaluation_completed"
    MEDIA_REPAIR_STARTED = "media_repair_started"
    MEDIA_REPAIR_COMPLETED = "media_repair_completed"
    HUMAN_MEDIA_DIRECTIVE_CREATED = "human_media_directive_created"
    MODEL_SERVICE_STATUS_CHANGED = "model_service_status_changed"
    RESOURCE_SNAPSHOT = "resource_snapshot"
    RESOURCE_LEASE_ACQUIRED = "resource_lease_acquired"
    RESOURCE_LEASE_RELEASED = "resource_lease_released"
    SCHEDULER_DECISION = "scheduler_decision"
    RESOURCE_PRESSURE = "resource_pressure"
    RESOURCE_ADMISSION_WAIT = "resource_admission_wait"
    REFERENCE_BOUND = "reference_bound"
    REFERENCE_UNBOUND = "reference_unbound"


class HumanGateType(StrEnum):
    STORY_APPROVAL = "story_approval"
    SCREENPLAY_APPROVAL = "screenplay_approval"
    SHOT_PLAN_APPROVAL = "shot_plan_approval"
    FINAL_CUT_APPROVAL = "final_cut_approval"
    AGENT_ESCALATION = "agent_escalation"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ProviderKind(StrEnum):
    LLM = "llm"
    VISION = "vision"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class ProviderErrorType(StrEnum):
    TIMEOUT = "timeout"
    REMOTE_COMPLETION_UNCERTAIN = "remote_completion_uncertain"
    UNAVAILABLE = "unavailable"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    RESOURCE_EXHAUSTED_OOM = "resource_exhausted_oom"
    INVALID_REQUEST = "invalid_request"
    MODEL_NOT_READY = "model_not_ready"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    GENERATION_FAILED = "generation_failed"
    CANCELLED = "cancelled"
    MEDIA_CORRUPT = "media_corrupt"
    INTERNAL = "internal"


class EvaluationLayer(StrEnum):
    TECHNICAL_QC = "technical_qc"
    VISUAL_SEMANTIC = "visual_semantic"
    CINEMATIC = "cinematic"


class IssueSeverity(StrEnum):
    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class EvaluationIssueType(StrEnum):
    IDENTITY_DRIFT = "identity_drift"
    SCENE_DRIFT = "scene_drift"
    MISSING_DETAIL = "missing_detail"
    HALLUCINATED_OBJECT = "hallucinated_object"
    EXTRA_CHARACTER = "extra_character"
    ACTION_FAILURE = "action_failure"
    CAMERA_MOTION_MISMATCH = "camera_motion_mismatch"
    CONTINUITY_ERROR = "continuity_error"
    CONTINUITY_CONFLICT = "continuity_conflict"
    VISUAL_ARTIFACT = "visual_artifact"
    WEAK_STORYTELLING = "weak_storytelling"
    STYLE_MISMATCH = "style_mismatch"
    GENERATION_FAILURE = "generation_failure"
    SHOT_DESIGN_FAILURE = "shot_design_failure"


class RepairActionType(StrEnum):
    STRENGTHEN_CHARACTER_REFERENCE = "strengthen_character_reference"
    STRENGTHEN_SCENE_REFERENCE = "strengthen_scene_reference"
    REWRITE_PROMPT = "rewrite_prompt"
    REGENERATE_FIRST_FRAME = "regenerate_first_frame"
    REGENERATE_LAST_FRAME = "regenerate_last_frame"
    CHANGE_GENERATION_STRATEGY = "change_generation_strategy"
    IMAGE_EDIT = "image_edit"
    REGENERATE = "regenerate"
    SPLIT_SHOT = "split_shot"
    DIRECTOR_REPLAN = "director_replan"
    REQUEST_HUMAN_REVIEW = "request_human_review"
