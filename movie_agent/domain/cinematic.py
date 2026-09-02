"""Model-agnostic cinematic intermediate representation (Cinematic IR)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, PositiveFloat, model_validator

from movie_agent.domain.base import ContractModel, JSONValue, new_id, utc_now
from movie_agent.domain.enums import (
    AnchorKind,
    CameraAngle,
    CameraMotionType,
    GenerationStrategyType,
    Orientation,
    PropCondition,
    QualityProfile,
    ResourceClass,
    ShotSize,
)


class Vector3(ContractModel):
    """Position in a named production coordinate system."""

    x: float
    y: float
    z: float = 0.0
    coordinate_space: str = "scene"


class Character(ContractModel):
    """Canonical character identity that should remain stable across scenes."""

    character_id: str = Field(default_factory=lambda: new_id("char"))
    name: str = Field(min_length=1)
    narrative_role: str = ""
    identity_description: str = ""
    personality: list[str] = Field(default_factory=list)
    appearance_constraints: list[str] = Field(default_factory=list)
    voice_constraints: list[str] = Field(default_factory=list)
    reference_artifact_ids: list[str] = Field(default_factory=list)


class CharacterState(ContractModel):
    """Mutable character facts at a precise continuity boundary."""

    character_id: str
    position: Vector3 | None = None
    orientation: Orientation | None = None
    pose: str | None = None
    wardrobe: str | None = None
    hair: str | None = None
    emotion: str | None = None
    held_prop_ids: list[str] = Field(default_factory=list)
    visible: bool = True
    damage_state: str | None = None


class Location(ContractModel):
    """Canonical place definition used by scenes."""

    location_id: str = Field(default_factory=lambda: new_id("loc"))
    name: str = Field(min_length=1)
    description: str = ""
    immutable_features: list[str] = Field(default_factory=list)
    reference_artifact_ids: list[str] = Field(default_factory=list)


class LocationState(ContractModel):
    """Mutable environment facts for a location at a continuity boundary."""

    location_id: str
    scene_state: list[str] = Field(default_factory=list)
    lighting_state: str | None = None
    weather: str | None = None
    damage_state: str | None = None
    time_of_day: str | None = None


class Prop(ContractModel):
    """Canonical story-relevant object."""

    prop_id: str = Field(default_factory=lambda: new_id("prop"))
    name: str = Field(min_length=1)
    description: str = ""
    continuity_critical: bool = False
    reference_artifact_ids: list[str] = Field(default_factory=list)


class PropState(ContractModel):
    """Mutable prop facts, including condition and possession."""

    prop_id: str
    condition: PropCondition = PropCondition.INTACT
    holder_character_id: str | None = None
    position: Vector3 | None = None
    visible: bool = True
    notes: str | None = None


class ContinuityState(ContractModel):
    """Complete deterministic state at the start or end of a shot."""

    character_states: dict[str, CharacterState] = Field(default_factory=dict)
    location_states: dict[str, LocationState] = Field(default_factory=dict)
    prop_states: dict[str, PropState] = Field(default_factory=dict)
    scene_state: list[str] = Field(default_factory=list)
    lighting_state: str | None = None
    weather: str | None = None
    damage_state: str | None = None
    previous_shot_id: str | None = None
    last_frame_artifact_id: str | None = None


class ContinuityChain(ContractModel):
    """Ordered shots that must preserve state and normally execute serially."""

    chain_id: str = Field(default_factory=lambda: new_id("chain"))
    label: str
    shot_ids: list[str] = Field(default_factory=list)
    initial_state: ContinuityState = Field(default_factory=ContinuityState)


class ShotNarrative(ContractModel):
    """Narrative purpose and dramatic beat of a shot."""

    purpose: str = Field(min_length=1)
    beat: str = Field(min_length=1)
    action_summary: str = ""
    dialogue: list[str] = Field(default_factory=list)


class CameraMotion(ContractModel):
    """Camera movement independent of provider prompt syntax."""

    motion_type: CameraMotionType = CameraMotionType.STATIC
    direction: str | None = None
    speed: str | None = None
    start_framing: str | None = None
    end_framing: str | None = None


class CompositionSpec(ContractModel):
    """Composition requirements for a shot."""

    framing: str = ""
    subject_placements: list[str] = Field(default_factory=list)
    depth_layers: list[str] = Field(default_factory=list)
    focus: str | None = None
    preserve: list[str] = Field(default_factory=list)


class CameraSpec(ContractModel):
    """Lens, size, angle, composition, and motion choices."""

    shot_size: ShotSize
    angle: CameraAngle = CameraAngle.EYE_LEVEL
    lens_mm: PositiveFloat | None = None
    lens_character: str | None = None
    composition: CompositionSpec = Field(default_factory=CompositionSpec)
    motion: CameraMotion = Field(default_factory=CameraMotion)


class LightingSpec(ContractModel):
    """Lighting intent for the shot, without renderer parameters."""

    setup: str
    key_direction: str | None = None
    contrast: str | None = None
    color_temperature: str | None = None
    practicals: list[str] = Field(default_factory=list)
    must_match_previous: bool = False


class PerformanceSpec(ContractModel):
    """Character performance over the duration of a shot."""

    character_id: str
    action: str
    emotion_start: str | None = None
    emotion_end: str | None = None
    blocking: str | None = None
    dialogue: str | None = None


class FrameAnchor(ContractModel):
    """A planned or materialized boundary-frame dependency."""

    kind: AnchorKind = AnchorKind.NONE
    source_artifact_id: str | None = None
    source_shot_id: str | None = None
    planned_state: ContinuityState | None = None
    description: str | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "FrameAnchor":
        """Require the reference needed by non-generated anchor kinds."""

        if self.kind == AnchorKind.PREVIOUS_SHOT_LAST_FRAME and not self.source_shot_id:
            raise ValueError("previous-shot anchor requires source_shot_id")
        if self.kind == AnchorKind.EXTERNAL_REFERENCE and not self.source_artifact_id:
            raise ValueError("external anchor requires source_artifact_id")
        return self


class FrameAnchors(ContractModel):
    """First/last boundary frame plan for one shot."""

    first_frame: FrameAnchor = Field(default_factory=FrameAnchor)
    last_frame: FrameAnchor = Field(default_factory=FrameAnchor)


class GenerationStrategy(ContractModel):
    """Provider-neutral plan for producing the shot."""

    strategy_type: GenerationStrategyType
    reason: str
    required_capabilities: list[str] = Field(default_factory=list)
    input_artifact_ids: list[str] = Field(default_factory=list)
    fallback_types: list[GenerationStrategyType] = Field(default_factory=list)


class Shot(ContractModel):
    """Production core for one model-agnostic cinematic shot."""

    shot_id: str = Field(default_factory=lambda: new_id("shot"))
    scene_id: str
    narrative: ShotNarrative
    duration_seconds: PositiveFloat
    camera: CameraSpec
    performances: list[PerformanceSpec] = Field(default_factory=list)
    lighting: LightingSpec
    visual_requirements: list[str] = Field(default_factory=list)
    reference_artifact_ids: list[str] = Field(default_factory=list)
    frame_anchors: FrameAnchors = Field(default_factory=FrameAnchors)
    continuity_chain_id: str | None = None
    previous_shot_id: str | None = None
    next_shot_id: str | None = None
    state_before: ContinuityState = Field(default_factory=ContinuityState)
    expected_state_after: ContinuityState = Field(default_factory=ContinuityState)
    generation_strategy: GenerationStrategy | None = None
    retry_budget: int = Field(default=2, ge=0)
    quality_profile: QualityProfile = QualityProfile.STANDARD


class Scene(ContractModel):
    """Narrative scene and references to its ordered production shots."""

    scene_id: str = Field(default_factory=lambda: new_id("scene"))
    title: str
    purpose: str
    location_id: str
    time_description: str
    character_ids: list[str] = Field(default_factory=list)
    prop_ids: list[str] = Field(default_factory=list)
    shot_ids: list[str] = Field(default_factory=list)
    initial_state: ContinuityState = Field(default_factory=ContinuityState)
    expected_final_state: ContinuityState = Field(default_factory=ContinuityState)


class PromptSection(ContractModel):
    """Named provider-independent prompt content compiled from Cinematic IR."""

    name: str
    content: str


class PromptPackage(ContractModel):
    """Compiled prompt payload at the provider boundary."""

    prompt_package_id: str = Field(default_factory=lambda: new_id("prompt"))
    compiler_id: str
    compiler_version: str
    positive_prompt: str
    negative_prompt: str = ""
    sections: list[PromptSection] = Field(default_factory=list)
    source_shot_id: str | None = None


class GenerationRequest(ContractModel):
    """Provider-facing request created only after strategy and prompt compilation."""

    request_id: str = Field(default_factory=lambda: new_id("req"))
    job_id: str
    task: str
    strategy: GenerationStrategy
    prompt_package: PromptPackage
    input_artifact_ids: list[str] = Field(default_factory=list)
    requested_output_type: str
    resource_class: ResourceClass = ResourceClass.MEDIUM
    parameters: dict[str, JSONValue] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
