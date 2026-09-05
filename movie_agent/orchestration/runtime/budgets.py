"""Deterministic per-scene budgets, shared by context and semantic validation."""

import math
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
