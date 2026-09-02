"""Deterministic continuity validation and state transition engine."""

from __future__ import annotations

from collections.abc import Iterable

from movie_agent.domain import (
    ContinuityState,
    EvaluationIssue,
    EvaluationIssueType,
    IssueSeverity,
    RepairActionType,
    Shot,
)


def _compare_fields(
    entity_label: str,
    previous: object,
    required: object,
    fields: Iterable[str],
    shot_id: str,
) -> list[EvaluationIssue]:
    issues: list[EvaluationIssue] = []
    required_fields = getattr(required, "model_fields_set", set())
    for field in fields:
        if field not in required_fields:
            continue
        before = getattr(previous, field)
        expected = getattr(required, field)
        if before != expected:
            issues.append(
                EvaluationIssue(
                    issue_type=EvaluationIssueType.CONTINUITY_CONFLICT,
                    severity=IssueSeverity.MAJOR,
                    message=(
                        f"{entity_label}.{field} conflicts with the previous boundary: "
                        f"{before!r} -> required {expected!r}"
                    ),
                    evidence=[f"previous={before!r}", f"required={expected!r}"],
                    suggested_action=RepairActionType.DIRECTOR_REPLAN,
                    shot_id=shot_id,
                )
            )
    return issues


def validate_transition(previous_state: ContinuityState, shot: Shot) -> list[EvaluationIssue]:
    """Validate a shot's declared start state before any generation is submitted."""

    required = shot.state_before
    issues: list[EvaluationIssue] = []

    for character_id, required_state in required.character_states.items():
        previous_character = previous_state.character_states.get(character_id)
        if previous_character is None:
            issues.append(
                EvaluationIssue(
                    issue_type=EvaluationIssueType.CONTINUITY_CONFLICT,
                    severity=IssueSeverity.MAJOR,
                    message=f"Character {character_id} has no prior state.",
                    evidence=["missing previous character state"],
                    suggested_action=RepairActionType.DIRECTOR_REPLAN,
                    shot_id=shot.shot_id,
                )
            )
            continue
        issues.extend(
            _compare_fields(
                f"character:{character_id}",
                previous_character,
                required_state,
                (
                    "position",
                    "orientation",
                    "pose",
                    "wardrobe",
                    "hair",
                    "emotion",
                    "held_prop_ids",
                    "visible",
                    "damage_state",
                ),
                shot.shot_id,
            )
        )

    for prop_id, required_state in required.prop_states.items():
        previous_prop = previous_state.prop_states.get(prop_id)
        if previous_prop is None:
            issues.append(
                EvaluationIssue(
                    issue_type=EvaluationIssueType.CONTINUITY_CONFLICT,
                    severity=IssueSeverity.MAJOR,
                    message=f"Prop {prop_id} has no prior state.",
                    evidence=["missing previous prop state"],
                    suggested_action=RepairActionType.DIRECTOR_REPLAN,
                    shot_id=shot.shot_id,
                )
            )
            continue
        issues.extend(
            _compare_fields(
                f"prop:{prop_id}",
                previous_prop,
                required_state,
                ("condition", "holder_character_id", "position", "visible", "notes"),
                shot.shot_id,
            )
        )

    for location_id, required_state in required.location_states.items():
        previous_location = previous_state.location_states.get(location_id)
        if previous_location is None:
            issues.append(
                EvaluationIssue(
                    issue_type=EvaluationIssueType.CONTINUITY_CONFLICT,
                    severity=IssueSeverity.MAJOR,
                    message=f"Location {location_id} has no prior state.",
                    evidence=["missing previous location state"],
                    suggested_action=RepairActionType.DIRECTOR_REPLAN,
                    shot_id=shot.shot_id,
                )
            )
            continue
        issues.extend(
            _compare_fields(
                f"location:{location_id}",
                previous_location,
                required_state,
                ("scene_state", "lighting_state", "weather", "damage_state", "time_of_day"),
                shot.shot_id,
            )
        )

    issues.extend(
        _compare_fields(
            "global",
            previous_state,
            required,
            ("scene_state", "lighting_state", "weather", "damage_state"),
            shot.shot_id,
        )
    )
    if shot.previous_shot_id and previous_state.previous_shot_id:
        if shot.previous_shot_id != previous_state.previous_shot_id:
            issues.append(
                EvaluationIssue(
                    issue_type=EvaluationIssueType.CONTINUITY_CONFLICT,
                    severity=IssueSeverity.CRITICAL,
                    message="Shot previous_shot_id does not match the supplied continuity state.",
                    evidence=[shot.previous_shot_id, previous_state.previous_shot_id],
                    suggested_action=RepairActionType.DIRECTOR_REPLAN,
                    shot_id=shot.shot_id,
                )
            )
    return issues


def detect_conflicts(previous_state: ContinuityState, shot: Shot) -> list[EvaluationIssue]:
    """Alias that emphasizes pre-generation conflict detection."""

    return validate_transition(previous_state, shot)


def _merge_entity(previous: object | None, update: object) -> object:
    if previous is None:
        return update.model_copy(deep=True)
    changes = {
        field: getattr(update, field)
        for field in update.model_fields_set
        if field != "schema_version"
    }
    return previous.model_copy(update=changes, deep=True)


def apply_shot_effects(state: ContinuityState, shot: Shot) -> ContinuityState:
    """Apply only explicitly declared end-state effects to a copied state."""

    result = state.model_copy(deep=True)
    effects = shot.expected_state_after

    for character_id, update in effects.character_states.items():
        result.character_states[character_id] = _merge_entity(
            result.character_states.get(character_id), update
        )
    for prop_id, update in effects.prop_states.items():
        result.prop_states[prop_id] = _merge_entity(result.prop_states.get(prop_id), update)
    for location_id, update in effects.location_states.items():
        result.location_states[location_id] = _merge_entity(
            result.location_states.get(location_id), update
        )

    for field in ("scene_state", "lighting_state", "weather", "damage_state"):
        if field in effects.model_fields_set:
            setattr(result, field, getattr(effects, field))
    result.previous_shot_id = shot.shot_id
    if effects.last_frame_artifact_id:
        result.last_frame_artifact_id = effects.last_frame_artifact_id
    return result


def derive_next_state(previous_state: ContinuityState, shot: Shot) -> ContinuityState:
    """Validate and derive the next boundary state, raising on conflicts."""

    issues = validate_transition(previous_state, shot)
    if issues:
        messages = "; ".join(issue.message for issue in issues)
        raise ValueError(f"CONTINUITY_CONFLICT: {messages}")
    return apply_shot_effects(previous_state, shot)
