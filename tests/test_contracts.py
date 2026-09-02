"""Contract serialization, validation, and JSON Schema coverage."""

import json

import pytest
from pydantic import ValidationError

from movie_agent.domain import (
    AnchorKind,
    CameraSpec,
    EventEnvelope,
    EventType,
    FrameAnchor,
    FrameAnchors,
    LightingSpec,
    Project,
    ProjectBrief,
    Shot,
    ShotNarrative,
    ShotSize,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
)


def sample_brief() -> ProjectBrief:
    """Return the smallest valid frontend-ready brief."""

    return ProjectBrief(
        title="The Last Signal",
        logline="An astronaut learns that Earth is gone.",
        story_description="A ship AI hides the loss of Earth from its last astronaut.",
        target_duration=90,
        sci_fi_setting=True,
        user_constraints=["No resurrection"],
        creative_freedom="high",
    )


def sample_shot() -> Shot:
    """Return a valid model-independent shot."""

    return Shot(
        shot_id="shot_001",
        scene_id="scene_001",
        narrative=ShotNarrative(purpose="Establish isolation", beat="The signal dies"),
        duration_seconds=5,
        camera=CameraSpec(shot_size=ShotSize.WIDE),
        lighting=LightingSpec(setup="Cold emergency practicals"),
        frame_anchors=FrameAnchors(
            first_frame=FrameAnchor(kind=AnchorKind.GENERATED),
            last_frame=FrameAnchor(kind=AnchorKind.GENERATED),
        ),
    )


def test_contract_round_trip_and_schema_version() -> None:
    project = Project(brief=sample_brief(), shots=[sample_shot()])
    payload = project.model_dump_json()
    restored = Project.model_validate_json(payload)

    assert restored == project
    assert restored.schema_version == "1.0.0"
    assert restored.brief.user_constraints == ["No resurrection"]
    assert json.loads(payload)["shots"][0]["schema_version"] == "1.0.0"


@pytest.mark.parametrize(
    "contract",
    [ProjectBrief, Project, Shot, WorkflowNode, WorkflowEdge, WorkflowGraph, EventEnvelope],
)
def test_contract_exports_json_schema(contract: type) -> None:
    schema = contract.model_json_schema()
    assert schema["title"] == contract.__name__
    assert "schema_version" in schema.get("properties", {})


def test_brief_rejects_invalid_density_and_unknown_fields() -> None:
    data = sample_brief().model_dump()
    data["dialogue_density"] = 1.5
    with pytest.raises(ValidationError):
        ProjectBrief.model_validate(data)

    data = sample_brief().model_dump()
    data["specific_model"] = "forbidden"
    with pytest.raises(ValidationError):
        ProjectBrief.model_validate(data)


def test_previous_frame_anchor_requires_source_shot() -> None:
    with pytest.raises(ValidationError, match="source_shot_id"):
        FrameAnchor(kind=AnchorKind.PREVIOUS_SHOT_LAST_FRAME)


def test_workflow_contract_rejects_cycle() -> None:
    first = WorkflowNode(node_id="a", node_type="plan", label="A", group="test", role="Showrunner")
    second = WorkflowNode(node_id="b", node_type="plan", label="B", group="test", role="Director")
    with pytest.raises(ValidationError, match="acyclic"):
        WorkflowGraph(
            project_id="project_1",
            nodes=[first, second],
            edges=[
                WorkflowEdge(source_node_id="a", target_node_id="b"),
                WorkflowEdge(source_node_id="b", target_node_id="a"),
            ],
        )


def test_event_envelope_is_json_serializable() -> None:
    event = EventEnvelope(
        event_type=EventType.PROJECT_CREATED,
        project_id="project_1",
        trace_id="trace_1",
        payload={"title": "Film", "counts": [1, 2]},
    )
    assert EventEnvelope.model_validate_json(event.model_dump_json()) == event
