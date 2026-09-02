"""Role and skill descriptors kept separate from model/provider deployment."""

from __future__ import annotations

from pydantic import Field

from movie_agent.domain import ContractModel


class RoleSpec(ContractModel):
    """Creative responsibility that can be implemented by a skill, node, or subgraph."""

    role_id: str
    label: str
    responsibility: str
    skills: list[str] = Field(default_factory=list)


CORE_ROLES: tuple[RoleSpec, ...] = (
    RoleSpec(
        role_id="showrunner",
        label="Showrunner",
        responsibility="Own production state, route work, enforce gates, budgets, and completion.",
        skills=["orchestration", "production_decisions"],
    ),
    RoleSpec(
        role_id="creative_producer",
        label="Creative Producer",
        responsibility="Expand the brief without overriding user constraints.",
        skills=["creative_expansion"],
    ),
    RoleSpec(
        role_id="story_architect",
        label="Story Architect",
        responsibility="Design narrative structure, beats, and character arcs.",
        skills=["story_planning"],
    ),
    RoleSpec(
        role_id="screenwriter",
        label="Screenwriter",
        responsibility="Create treatment, screenplay, action, and dialogue.",
        skills=["screenwriting"],
    ),
    RoleSpec(
        role_id="director",
        label="Director",
        responsibility="Translate story intent into scenes and performance decisions.",
        skills=["scene_planning", "storyboard_direction"],
    ),
    RoleSpec(
        role_id="cinematographer",
        label="Cinematographer",
        responsibility="Design shots, lenses, movement, composition, and lighting.",
        skills=["shot_planning", "camera_design"],
    ),
    RoleSpec(
        role_id="visual_director",
        label="Visual Director",
        responsibility="Maintain the visual bible, references, palette, and style system.",
        skills=["visual_bible", "asset_planning"],
    ),
    RoleSpec(
        role_id="continuity_supervisor",
        label="Continuity Supervisor",
        responsibility="Validate deterministic state transitions before generation.",
        skills=["continuity", "technical_qc"],
    ),
    RoleSpec(
        role_id="sound_post_director",
        label="Sound/Post Director",
        responsibility="Plan sound, audio, editorial assembly, and final delivery.",
        skills=["audio", "postproduction"],
    ),
    RoleSpec(
        role_id="critic",
        label="Critic",
        responsibility="Produce evidenced semantic and cinematic evaluations.",
        skills=["visual_semantic_critique", "cinematic_critique"],
    ),
    RoleSpec(
        role_id="repair_planner",
        label="Repair Planner",
        responsibility="Route structured issues through finite repairs or escalation.",
        skills=["issue_classification", "repair_planning"],
    ),
)
