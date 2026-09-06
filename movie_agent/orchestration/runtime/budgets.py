"""Deterministic per-scene budgets, shared by context and semantic validation."""

import math
import json
from movie_agent.domain import ContractModel, Project, Scene


class SceneShotBudget(ContractModel):
    scene_duration_budget: float
    recommended_min_shots: int
    max_shots: int
    remaining_duration: float
    remaining_shot_budget: int
    remaining_scene_count: int


def scene_shot_budget(project: Project, scene: Scene) -> SceneShotBudget:
    if scene.scene_id not in {s.scene_id for s in project.scenes}:
        raise ValueError("Current scene is not in the project")
    others = [s for s in project.shots if s.scene_id != scene.scene_id]
    completed = {s.scene_id for s in others}
    pending = sum(s.scene_id not in completed for s in project.scenes)
    remaining_duration = project.brief.target_duration - sum(s.duration_seconds for s in others)
    remaining_shots = project.brief.max_shots - len(others)
    if remaining_duration <= 0 or remaining_shots < pending:
        raise ValueError("Insufficient remaining duration/shot budget for unfinished scenes")
    duration = remaining_duration / pending
    # No explicit Scene duration exists in the IR. Divide remaining time fairly;
    # reserve capacity for every unfinished scene and avoid excessive short cuts.
    maximum = min(remaining_shots // pending, max(1, math.ceil(duration / 4)))
    return SceneShotBudget(scene_duration_budget=duration,
        recommended_min_shots=min(maximum, max(1, math.ceil(duration / 8))), max_shots=maximum,
        remaining_duration=remaining_duration, remaining_shot_budget=remaining_shots,
        remaining_scene_count=pending)


def shot_output_budget(max_shots: int) -> int:
    """Allow full boundary states plus cinematic fields, capped for one scene."""
    return min(12000, 2000 + 5000 * max_shots)


def planning_output_budget(role: str, project: Project, *, scene: Scene | None = None) -> int | None:
    """Conservative output ceilings, not schema/scene/shot truncation.

    Reserve space for verbatim ledgers; never discard fields or existing scenes.
    Large plans still reach the original role ceiling. Small plans avoid paying
    the maximum simply because the contract supports a large film.
    """
    ledger_chars = len(json.dumps(project.brief.user_constraints + project.brief.must_preserve
        + project.brief.world_rules + (project.story_bible.immutable_facts if project.story_bible else []),
        ensure_ascii=False))
    reserve = math.ceil(ledger_chars / 2)
    if role == 'screenwriter':
        scenes = min(project.brief.max_shots, max(1, math.ceil(project.brief.target_duration / 20)))
        characters = project.brief.desired_character_count or len(project.brief.character_descriptions)
        return min(8192, max(4096, 2048 + 768 * scenes + 256 * characters + reserve))
    if role == 'director':
        scenes = project.screenplay.scenes if project.screenplay else []
        memberships = sum(len(s.character_ids) + len(s.prop_ids) + 1 for s in scenes)
        return min(8192, 4096 + 1500 * max(0, len(scenes) - 1) + 256 * memberships + reserve)
    if role == 'cinematographer' and scene is not None:
        shots = scene_shot_budget(project, scene).max_shots
        return min(shot_output_budget(shots), 7000 + 1250 * max(0, shots - 1) + reserve)
    return None
