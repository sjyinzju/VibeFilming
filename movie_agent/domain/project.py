"""Project brief, creative bibles, and canonical project state contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, PositiveFloat

from movie_agent.domain.base import ContractModel, JSONValue, new_id, utc_now
from movie_agent.domain.cinematic import (
    Character,
    ContinuityChain,
    Location,
    Prop,
    Scene,
    Shot,
)
from movie_agent.domain.enums import (
    AspectRatio,
    CreativeFreedomLevel,
    Pacing,
    QualityProfile,
)


class ProjectBrief(ContractModel):
    """Frontend-ready user intent, with hard constraints separate from freedom."""

    title: str = Field(min_length=1)
    logline: str = Field(min_length=1)
    story_description: str = Field(min_length=1)
    target_duration: PositiveFloat = Field(description="Target film duration in seconds.")
    output_language: str = "en"
    genre: list[str] = Field(default_factory=list)
    audience: str = "general"
    rating: str = "unrated"

    theme: list[str] = Field(default_factory=list)
    narrative_style: str = ""
    ending_preference: str = ""
    pacing: Pacing = Pacing.MODERATE
    dialogue_density: float = Field(default=0.5, ge=0.0, le=1.0)
    creative_freedom: CreativeFreedomLevel = CreativeFreedomLevel.MEDIUM

    desired_character_count: int = Field(default=1, ge=0)
    character_descriptions: list[str] = Field(default_factory=list)
    relationship_constraints: list[str] = Field(default_factory=list)

    locations: list[str] = Field(default_factory=list)
    time_period: str = ""
    sci_fi_setting: bool = False
    world_rules: list[str] = Field(default_factory=list)
    prohibited_elements: list[str] = Field(default_factory=list)

    visual_style: str = ""
    reference_movies: list[str] = Field(default_factory=list)
    reference_images: list[str] = Field(
        default_factory=list,
        description="Artifact IDs or external references supplied by the user.",
    )
    palette: list[str] = Field(default_factory=list)
    lighting: str = ""
    realism: str = ""
    aspect_ratio: AspectRatio = AspectRatio.RATIO_16_9
    resolution: str = "1920x1080"
    fps: PositiveFloat = 24

    preferred_shot_style: list[str] = Field(default_factory=list)
    preferred_camera_motion: list[str] = Field(default_factory=list)
    preferred_lenses: list[PositiveFloat] = Field(
        default_factory=list,
        description="Preferred focal lengths in millimetres.",
    )
    cutting_style: str = ""
    composition_preferences: list[str] = Field(default_factory=list)

    dialogue_style: str = ""
    voice_style: str = ""
    music_style: str = ""
    ambience_style: str = ""
    sound_design: str = ""

    quality_level: QualityProfile = QualityProfile.STANDARD
    max_shots: int = Field(default=24, ge=1)
    max_retry: int = Field(default=2, ge=0)
    generation_budget: float = Field(default=0.0, ge=0.0)
    user_constraints: list[str] = Field(default_factory=list)
    must_preserve: list[str] = Field(default_factory=list)


class CreativeDirection(ContractModel):
    """Showrunner-approved creative interpretation that cannot override constraints."""

    premise_expansion: str
    emotional_arc: str
    tone: str
    motifs: list[str] = Field(default_factory=list)
    creative_choices: list[str] = Field(default_factory=list)
    preserved_user_constraints: list[str] = Field(default_factory=list)


class StoryBible(ContractModel):
    """Canonical narrative facts and arcs used by all creative roles."""

    story_bible_id: str = Field(default_factory=lambda: new_id("storybible"))
    synopsis: str
    themes: list[str] = Field(default_factory=list)
    acts: list[str] = Field(default_factory=list)
    character_arcs: dict[str, str] = Field(default_factory=dict)
    world_facts: list[str] = Field(default_factory=list)
    immutable_facts: list[str] = Field(default_factory=list)


class VisualBible(ContractModel):
    """Canonical visual language and consistency constraints."""

    visual_bible_id: str = Field(default_factory=lambda: new_id("visualbible"))
    style_statement: str
    palette: list[str] = Field(default_factory=list)
    lighting_rules: list[str] = Field(default_factory=list)
    composition_rules: list[str] = Field(default_factory=list)
    camera_rules: list[str] = Field(default_factory=list)
    continuity_rules: list[str] = Field(default_factory=list)
    prohibited_visuals: list[str] = Field(default_factory=list)


class CanonicalProjectState(ContractModel):
    """Confirmed production facts, separate from preferences and history."""

    confirmed_facts: list[str] = Field(default_factory=list)
    current_scene_id: str | None = None
    current_shot_id: str | None = None
    completed_node_ids: list[str] = Field(default_factory=list)


class CreativeMemory(ContractModel):
    """Learned or user-stated creative preferences."""

    preferences: list[str] = Field(default_factory=list)
    rejected_choices: list[str] = Field(default_factory=list)
    notes_by_role: dict[str, list[str]] = Field(default_factory=dict)


class GenerationHistoryEntry(ContractModel):
    """Outcome of a provider-neutral generation strategy attempt."""

    job_id: str
    shot_id: str | None = None
    strategy_type: str
    succeeded: bool
    provider_id: str | None = None
    issue_codes: list[str] = Field(default_factory=list)
    metadata: dict[str, JSONValue] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class GenerationHistory(ContractModel):
    """Past strategy outcomes used for future routing without vector storage."""

    entries: list[GenerationHistoryEntry] = Field(default_factory=list)


class Project(ContractModel):
    """Aggregate root for one long-running movie production."""

    project_id: str = Field(default_factory=lambda: new_id("project"))
    brief: ProjectBrief
    creative_direction: CreativeDirection | None = None
    story_bible: StoryBible | None = None
    visual_bible: VisualBible | None = None
    characters: list[Character] = Field(default_factory=list)
    locations: list[Location] = Field(default_factory=list)
    props: list[Prop] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    shots: list[Shot] = Field(default_factory=list)
    continuity_chains: list[ContinuityChain] = Field(default_factory=list)
    canonical_state: CanonicalProjectState = Field(default_factory=CanonicalProjectState)
    creative_memory: CreativeMemory = Field(default_factory=CreativeMemory)
    generation_history: GenerationHistory = Field(default_factory=GenerationHistory)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
