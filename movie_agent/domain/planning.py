"""Typed creative intermediates that reuse existing entities, scenes, and shots."""

from pydantic import Field
from movie_agent.domain.base import ContractModel, new_id
from movie_agent.domain.cinematic import Character, Location, Prop, Scene, Shot, ContinuityChain


class Dialogue(ContractModel):
    """One spoken screenplay line referring to a declared character."""

    character_id: str
    text: str


class ScreenplayScene(ContractModel):
    """Dramatic scene before cinematic shot breakdown."""

    scene_id: str
    location_id: str
    title: str
    action: list[str] = Field(min_length=1)
    dialogue: list[Dialogue] = Field(default_factory=list)
    character_ids: list[str] = Field(default_factory=list)
    prop_ids: list[str] = Field(default_factory=list)


class PlanningCommitments(ContractModel):
    """Explicit carry-forward of hard constraints and immutable narrative facts."""

    preserved_constraints: list[str]
    immutable_facts: list[str]


class Screenplay(PlanningCommitments):
    """Typed script with canonical entity declarations and scene actions/dialogue."""

    screenplay_id: str = Field(default_factory=lambda: new_id("screenplay"))
    title: str
    characters: list[Character]
    locations: list[Location]
    props: list[Prop] = Field(default_factory=list)
    scenes: list[ScreenplayScene] = Field(min_length=1)


class ScenePlan(PlanningCommitments):
    """Director output containing existing Scene contracts, never alternate scene JSON."""

    scenes: list[Scene] = Field(min_length=1)


class ShotPlan(PlanningCommitments):
    """One scene's cinematography using existing Shot and ContinuityChain contracts."""

    scene_id: str
    shots: list[Shot] = Field(min_length=1)
    continuity_chains: list[ContinuityChain] = Field(min_length=1)
