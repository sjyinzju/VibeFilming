"""Regression coverage for Core-owned canonical state and LLM-owned local deltas."""

import json
import pytest
from pydantic import ValidationError

from movie_agent.domain import (
    CameraSpec, Character, CharacterState, ContinuityState, LightingSpec, Location,
    LocationState, Project, ProjectBrief, Prop, PropState, Scene, ShotNarrative, ShotPlan,
    ShotSize, StoryBible, Vector3,
)
from movie_agent.domain.enums import PropCondition
from movie_agent.orchestration.runtime.cinematographer_drafts import (
    CinematographerDraftMapper, PropLocalStateDraft, ShotDraft, ShotLocalStateDraft,
    ShotPlanDraft,
)
from movie_agent.orchestration.runtime.runner import StructuredOutputAdapter
from movie_agent.orchestration.runtime.validation import RoleOutputValidator


def scene2_fixture():
    brief = ProjectBrief(title="The Last Signal", logline="Truth", story_description="Choice",
        target_duration=6, max_shots=2, must_preserve=["Truth wins"])
    project = Project(brief=brief, story_bible=StoryBible(synopsis="Truth",
        immutable_facts=["Truth wins"]))
    project.characters = [Character(character_id="MARA", name="Mara")]
    project.locations = [Location(location_id="CABIN", name="Cabin")]
    project.props = [Prop(prop_id="CONSOLE", name="Console"),
                     Prop(prop_id="OVERRIDE", name="Override")]
    origin = Vector3(x=0, y=0, z=0, coordinate_space="CABIN")
    before = ContinuityState(character_states={"MARA": CharacterState(character_id="MARA",
        position=origin, held_prop_ids=["OVERRIDE"])},
        location_states={"CABIN": LocationState(location_id="CABIN")},
        prop_states={"CONSOLE": PropState(prop_id="CONSOLE", position=origin),
                     "OVERRIDE": PropState(prop_id="OVERRIDE", position=origin,
                         holder_character_id="MARA")})
    after = before.model_copy(deep=True)
    after.prop_states["OVERRIDE"].condition = PropCondition.DAMAGED
    after.prop_states["OVERRIDE"].holder_character_id = None
    after.character_states["MARA"].held_prop_ids = []
    scene = Scene(scene_id="SCN-002-CABIN", title="Truth", purpose="Choose",
        location_id="CABIN", time_description="night", character_ids=["MARA"],
        prop_ids=["OVERRIDE"], initial_state=before, expected_final_state=after)
    project.scenes = [scene]
    return project, scene


def draft(*deltas):
    return ShotPlanDraft(preserved_constraints=["Truth wins"], immutable_facts=["Truth wins"],
        shots=[ShotDraft(narrative=ShotNarrative(purpose="Reveal", beat=f"Beat {index}"),
            duration_seconds=6 / len(deltas), camera=CameraSpec(shot_size=ShotSize.CLOSE_UP),
            lighting=LightingSpec(setup="console practical"), local_state_delta=delta)
            for index, delta in enumerate(deltas, 1)])


def test_canonical_snapshot_cannot_be_expressed_in_output_contract():
    project, scene = scene2_fixture()
    schema = StructuredOutputAdapter().response_format(ShotPlanDraft, max_scene_shots=2, scene=scene)
    encoded = json.dumps(schema)
    assert "continuity_chains" not in encoded
    assert "initial_state" not in encoded
    payload = draft(ShotLocalStateDraft()).model_dump(mode="json")
    payload["continuity_chains"] = [{"initial_state": {"prop_states": {"OVERRIDE": {}}}}]
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ShotPlanDraft.model_validate(payload)


@pytest.mark.parametrize("entity_id,code", [("CONSOLE", "broken_reference"),
                                              ("UNKNOWN", "unknown_entity_id")])
def test_ambient_and_unknown_local_state_are_rejected(entity_id, code):
    project, scene = scene2_fixture()
    candidate = draft(ShotLocalStateDraft(prop_updates=[PropLocalStateDraft(prop_id=entity_id)]))
    report = CinematographerDraftMapper().validate_local(candidate, project, scene)
    assert not report.valid and report.issues[0].code.value == code


def test_scene_local_delta_inherits_ambient_canonical_state_and_maps_complete_ir():
    project, scene = scene2_fixture()
    candidate = draft(ShotLocalStateDraft(character_updates=[{
        "character_id": "MARA", "held_prop_ids": []}], prop_updates=[PropLocalStateDraft(
        prop_id="OVERRIDE", condition=PropCondition.DAMAGED, clear_holder_character_id=True)]))
    plan, local = CinematographerDraftMapper().map(candidate, project, scene)
    assert local.valid and isinstance(plan, ShotPlan)
    shot = plan.shots[0]
    assert set(shot.state_before.prop_states) == {"CONSOLE", "OVERRIDE"}
    assert set(shot.expected_state_after.prop_states) == {"CONSOLE", "OVERRIDE"}
    assert shot.expected_state_after.prop_states["CONSOLE"] == scene.initial_state.prop_states["CONSOLE"]
    assert shot.expected_state_after.prop_states["OVERRIDE"].condition == PropCondition.DAMAGED
    assert shot.expected_state_after.prop_states["OVERRIDE"].holder_character_id is None
    assert plan.continuity_chains[0].initial_state == scene.initial_state
    assert plan.continuity_chains[0].shot_ids == [shot.shot_id]
    assert RoleOutputValidator().validate(plan, project, scene=scene).valid


def test_local_vector_update_preserves_nested_contract_type():
    project, scene = scene2_fixture()
    candidate = draft(ShotLocalStateDraft(character_updates=[{
        "character_id": "MARA", "position": Vector3(x=0, y=0, z=0,
            coordinate_space="CABIN"), "held_prop_ids": []}], prop_updates=[{
        "prop_id": "OVERRIDE", "position": Vector3(x=0, y=0, z=0,
            coordinate_space="CABIN"), "condition": PropCondition.DAMAGED,
        "clear_holder_character_id": True}]))

    plan, local = CinematographerDraftMapper().map(candidate, project, scene)

    assert local.valid
    assert isinstance(plan.shots[0].expected_state_after.character_states["MARA"].position, Vector3)
    assert isinstance(plan.shots[0].expected_state_after.prop_states["OVERRIDE"].position, Vector3)
    assert RoleOutputValidator().validate(plan, project, scene=scene).valid


def test_multiple_shot_deltas_chain_canonical_states_and_topology():
    project, scene = scene2_fixture()
    candidate = draft(
        ShotLocalStateDraft(prop_updates=[PropLocalStateDraft(prop_id="OVERRIDE", notes="grasped")]),
        ShotLocalStateDraft(character_updates=[{"character_id": "MARA", "held_prop_ids": []}],
            prop_updates=[PropLocalStateDraft(prop_id="OVERRIDE", condition=PropCondition.DAMAGED,
                notes="grasped", clear_holder_character_id=True)]))
    plan, local = CinematographerDraftMapper().map(candidate, project, scene)
    assert local.valid
    first, second = plan.shots
    assert first.expected_state_after == second.state_before
    assert first.next_shot_id == second.shot_id and second.previous_shot_id == first.shot_id
    assert second.expected_state_after.prop_states["CONSOLE"].condition == PropCondition.INTACT
    assert RoleOutputValidator().validate(plan, project, scene=scene).valid


def test_duplicate_conflicting_update_rejected_before_mapping():
    project, scene = scene2_fixture()
    candidate = draft(ShotLocalStateDraft(prop_updates=[
        PropLocalStateDraft(prop_id="OVERRIDE", notes="one"),
        PropLocalStateDraft(prop_id="OVERRIDE", notes="two")]))
    mapped, report = CinematographerDraftMapper().map(candidate, project, scene)
    assert mapped is None and not report.valid
    assert report.issues[0].code.value == "semantic_conflict"
