"""Continuity, frame planning, strategy, and prompt compiler tests."""

import pytest

from movie_agent.cinematic import (
    GenerationStrategyPlanner,
    GenericPromptCompiler,
    RuleBasedFramePlanner,
    apply_shot_effects,
    derive_next_state,
    validate_transition,
)
from movie_agent.domain import (
    AnchorKind,
    CameraSpec,
    CharacterState,
    GenerationStrategyType,
    LightingSpec,
    PropCondition,
    PropState,
    ProviderCapability,
    ProviderKind,
    ResourceClass,
    Shot,
    ShotNarrative,
    ShotSize,
    ContinuityState,
    Vector3,
)


def make_shot(**changes) -> Shot:
    values = {
        "shot_id": "shot_2",
        "scene_id": "scene_1",
        "narrative": ShotNarrative(purpose="Continue action", beat="The astronaut reacts"),
        "duration_seconds": 4,
        "camera": CameraSpec(shot_size=ShotSize.MEDIUM),
        "lighting": LightingSpec(setup="Cold cabin practicals"),
        "continuity_chain_id": "chain_1",
        "previous_shot_id": "shot_1",
    }
    values.update(changes)
    return Shot(**values)


def test_broken_prop_conflict_is_found_before_generation() -> None:
    previous = ContinuityState(
        prop_states={"cup": PropState(prop_id="cup", condition=PropCondition.BROKEN)},
        previous_shot_id="shot_1",
    )
    shot = make_shot(
        state_before=ContinuityState(
            prop_states={"cup": PropState(prop_id="cup", condition=PropCondition.INTACT)}
        )
    )

    issues = validate_transition(previous, shot)

    assert len(issues) == 1
    assert issues[0].issue_type.value == "continuity_conflict"
    assert "broken" in issues[0].message
    assert "intact" in issues[0].message


def test_apply_effects_and_derive_next_state() -> None:
    previous = ContinuityState(
        prop_states={"cup": PropState(prop_id="cup", condition=PropCondition.INTACT)},
        previous_shot_id="shot_1",
    )
    shot = make_shot(
        state_before=ContinuityState(
            prop_states={"cup": PropState(prop_id="cup", condition=PropCondition.INTACT)}
        ),
        expected_state_after=ContinuityState(
            prop_states={"cup": PropState(prop_id="cup", condition=PropCondition.BROKEN)}
        ),
    )

    next_state = derive_next_state(previous, shot)

    assert next_state.prop_states["cup"].condition == PropCondition.BROKEN
    assert next_state.previous_shot_id == "shot_2"
    assert previous.prop_states["cup"].condition == PropCondition.INTACT
    assert apply_shot_effects(previous, shot) == next_state


def test_state_merge_preserves_nested_contract_types() -> None:
    previous = ContinuityState(
        character_states={
            "hero": CharacterState(
                character_id="hero",
                position=Vector3(x=1, y=2),
                wardrobe="coat",
            )
        },
        previous_shot_id="shot_1",
    )
    shot = make_shot(
        state_before=previous,
        expected_state_after=ContinuityState(
            character_states={
                "hero": CharacterState(character_id="hero", emotion="calm")
            }
        ),
    )

    next_state = derive_next_state(previous, shot)

    assert isinstance(next_state.character_states["hero"].position, Vector3)
    assert next_state.character_states["hero"].position == Vector3(x=1, y=2)


def test_derive_rejects_conflict() -> None:
    previous = ContinuityState(
        prop_states={"cup": PropState(prop_id="cup", condition=PropCondition.BROKEN)},
        previous_shot_id="shot_1",
    )
    shot = make_shot(
        state_before=ContinuityState(
            prop_states={"cup": PropState(prop_id="cup", condition=PropCondition.INTACT)}
        )
    )
    with pytest.raises(ValueError, match="CONTINUITY_CONFLICT"):
        derive_next_state(previous, shot)


def test_frame_planner_chains_previous_last_frame() -> None:
    previous_shot = make_shot(shot_id="shot_1", previous_shot_id=None)
    shot = make_shot()
    anchors = RuleBasedFramePlanner().plan(
        shot,
        previous_shot,
        ContinuityState(last_frame_artifact_id="artifact_frame_1"),
    )

    assert anchors.first_frame.kind == AnchorKind.PREVIOUS_SHOT_LAST_FRAME
    assert anchors.first_frame.source_shot_id == "shot_1"
    assert anchors.first_frame.source_artifact_id == "artifact_frame_1"
    assert anchors.last_frame.kind == AnchorKind.GENERATED


def test_strategy_prefers_first_last_frame_when_supported() -> None:
    shot = make_shot()
    shot.frame_anchors = RuleBasedFramePlanner().plan(shot)
    capabilities = [
        ProviderCapability(
            provider_id="mock-video",
            kind=ProviderKind.VIDEO,
            tasks=["shot_generation"],
            generation_strategies=[
                GenerationStrategyType.TEXT_TO_VIDEO,
                GenerationStrategyType.FIRST_LAST_FRAME_TO_VIDEO,
            ],
            accepts_first_frame=True,
            accepts_last_frame=True,
            resource_classes=[ResourceClass.MEDIUM],
        )
    ]

    strategy = GenerationStrategyPlanner().plan(shot, capabilities)

    assert strategy.strategy_type == GenerationStrategyType.FIRST_LAST_FRAME_TO_VIDEO
    assert "first_frame" in strategy.required_capabilities
    assert GenerationStrategyType.TEXT_TO_VIDEO in strategy.fallback_types


def test_generic_compiler_is_boundary_and_does_not_mutate_shot() -> None:
    shot = make_shot()
    before = shot.model_dump()
    package = GenericPromptCompiler().compile(shot)

    assert package.source_shot_id == shot.shot_id
    assert "[camera]" in package.positive_prompt
    assert shot.model_dump() == before
    assert "provider" not in shot.model_dump()
