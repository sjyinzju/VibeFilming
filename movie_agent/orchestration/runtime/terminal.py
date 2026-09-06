"""Input-only canonical terminal constraints and bounded shot-local repair transport."""

import json
from typing import Literal
from pydantic import Field
from movie_agent.domain import ContractModel, JSONValue, ContinuityState, PerformanceSpec
from .cinematographer_drafts import ShotPlanDraft, ShotLocalStateDraft, FramePlanningDraft


class TerminalRequirement(ContractModel):
    kind: Literal["character", "location", "prop", "global"]
    entity_id: str | None
    field: str
    observed: JSONValue
    required: JSONValue
    canonical_path: str
    editable_path: str


class RequiredTerminalDelta(ContractModel):
    """Computed by Core, never accepted as model-owned output."""
    scene_id: str
    authority: Literal["Scene.expected_final_state"] = "Scene.expected_final_state"
    requirements: list[TerminalRequirement]


def terminal_delta(scene, current: ContinuityState, shot_index: int | str = "last"):
    requirements = []
    for kind, fields in (
        ("character", ("position", "orientation", "pose", "wardrobe", "hair", "emotion", "held_prop_ids", "visible", "damage_state")),
        ("prop", ("condition", "holder_character_id", "position", "visible", "notes")),
        ("location", ("scene_state", "lighting_state", "weather", "damage_state", "time_of_day")),
        ("global", ("scene_state", "lighting_state", "weather", "damage_state")),
    ):
        targets = ({None: scene.expected_final_state} if kind == "global"
                   else getattr(scene.expected_final_state, kind + "_states"))
        previous = {None: current} if kind == "global" else getattr(current, kind + "_states")
        for identity, target in targets.items():
            before = previous.get(identity)
            for field in fields:
                if field not in target.model_fields_set:
                    continue
                required = target.model_dump(mode="json")[field]
                observed = before.model_dump(mode="json")[field] if before else None
                if before is not None and observed == required:
                    continue
                local = field if kind == "global" else f"{kind}_updates[{kind}_id={identity}].{field}"
                if kind == "prop" and field == "holder_character_id" and required is None:
                    local = f"prop_updates[prop_id={identity}].clear_holder_character_id (set true)"
                canonical = field if kind == "global" else f"{kind}_states.{identity}.{field}"
                requirements.append(TerminalRequirement(kind=kind, entity_id=identity, field=field,
                    observed=observed, required=required, canonical_path=canonical,
                    editable_path=f"shots[{shot_index}].local_state_delta.{local}"))
    return RequiredTerminalDelta(scene_id=scene.scene_id, requirements=requirements)


class TerminalShotRepair(ContractModel):
    shot_index: int = Field(ge=0)
    performances: list[PerformanceSpec]
    local_state_delta: ShotLocalStateDraft
    frame_planning: FramePlanningDraft


class TerminalRepairDraft(ContractModel):
    """Only realization/state fields of the terminal shot; no camera/timing/topology edits."""
    shot_repairs: list[TerminalShotRepair] = Field(min_length=1, max_length=1)


def merge_terminal_repair(base: str, raw: str) -> str:
    draft = ShotPlanDraft.model_validate_json(base)
    patch = TerminalRepairDraft.model_validate_json(raw)
    edit = patch.shot_repairs[0]
    if edit.shot_index != len(draft.shots) - 1:
        raise ValueError("Terminal repair may only edit the final shot")
    shot = draft.shots[edit.shot_index]
    shot.performances = edit.performances
    shot.local_state_delta = edit.local_state_delta
    shot.frame_planning = edit.frame_planning
    return draft.model_dump_json()


def terminal_repair_prompt(original, base, scene, current, report):
    draft = ShotPlanDraft.model_validate_json(base)
    index = len(draft.shots) - 1
    return json.dumps({"original_task": original, "previous_shot": draft.shots[index].model_dump(mode="json"),
        "shot_index": index, "required_terminal_delta": terminal_delta(scene, current, index).model_dump(mode="json"),
        "validation_errors": report.model_dump(mode="json"),
        "instruction": "Return TerminalRepairDraft, not a full plan. Scene.expected_final_state is authoritative. "
        "Fix the exact entity/field at each editable_path using the required canonical value verbatim. "
        "Do not swap characters' targets or treat a terminal target as emotion_start. "
        "Update affected performance progression (emotion_end/action) and final frame to realize that target. "
        "Respect the canonical shot start when setting emotion_start/first frame. Preserve unrelated characters, "
        "local deltas and creative choices. Camera, duration, narrative and all other shots remain unchanged in Core. "
        "Return the complete three editable fields for this shot; retain all unaffected entries."}, ensure_ascii=False)


def is_terminal_only(report):
    return bool(report.issues) and all(
        issue.code == "semantic_conflict" and issue.message.startswith("Scene ending:")
        for issue in report.issues)
