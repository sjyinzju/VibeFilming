"""Three-layer quality and finite repair policy tests."""

from movie_agent.domain import (
    Artifact,
    ArtifactType,
    CameraSpec,
    Evaluation,
    EvaluationIssue,
    EvaluationIssueType,
    EvaluationLayer,
    IssueSeverity,
    LightingSpec,
    RepairActionType,
    Shot,
    ShotNarrative,
    ShotSize,
)
from movie_agent.quality import (
    FailureClass,
    IssueClassifier,
    MockCinematicCritic,
    MockVisualSemanticCritic,
    RepairPlanner,
    TechnicalQC,
)


def shot() -> Shot:
    return Shot(
        shot_id="shot_2",
        scene_id="scene_1",
        narrative=ShotNarrative(purpose="Reveal", beat="The truth appears"),
        duration_seconds=5,
        camera=CameraSpec(shot_size=ShotSize.CLOSE_UP),
        lighting=LightingSpec(setup="Monitor glow"),
        retry_budget=1,
    )


def artifact(version: int = 1) -> Artifact:
    return Artifact(
        artifact_id="video_shot_2",
        artifact_type=ArtifactType.VIDEO,
        uri=f"file:///shot_2_v{version}.placeholder",
        version=version,
        metadata={"duration_seconds": 5, "width": 1920, "height": 1080},
    )


def test_technical_qc_and_mock_critic_fail_once() -> None:
    target_shot = shot()
    critic = MockVisualSemanticCritic({target_shot.shot_id})

    assert TechnicalQC().evaluate(target_shot, artifact()).passed
    first = critic.evaluate(target_shot, artifact())
    second = critic.evaluate(target_shot, artifact(2))
    cinematic = MockCinematicCritic().evaluate(target_shot, artifact(2))

    assert not first.passed and first.issues[0].issue_type == EvaluationIssueType.ACTION_FAILURE
    assert second.passed
    assert cinematic.passed


def test_repair_routing_and_failure_classification() -> None:
    issue = EvaluationIssue(
        issue_type=EvaluationIssueType.IDENTITY_DRIFT,
        severity=IssueSeverity.MAJOR,
        message="Face changed",
        shot_id="shot_2",
    )
    evaluation = Evaluation(
        layer=EvaluationLayer.VISUAL_SEMANTIC,
        target_shot_id="shot_2",
        score=0.3,
        passed=False,
        issues=[issue],
    )
    plan = RepairPlanner().plan(evaluation, retry_budget=2, retry_count=0)

    assert IssueClassifier().classify(issue) == FailureClass.GENERATION
    assert plan.actions[0].action_type == RepairActionType.STRENGTHEN_CHARACTER_REFERENCE
    assert not plan.exhausted


def test_retry_budget_exhaustion_escalates_generation_failure() -> None:
    issue = EvaluationIssue(
        issue_type=EvaluationIssueType.GENERATION_FAILURE,
        severity=IssueSeverity.CRITICAL,
        message="Provider output unusable",
        shot_id="shot_2",
    )
    evaluation = Evaluation(
        layer=EvaluationLayer.TECHNICAL_QC,
        target_shot_id="shot_2",
        score=0,
        passed=False,
        issues=[issue],
    )
    plan = RepairPlanner().plan(evaluation, retry_budget=1, retry_count=1)

    assert plan.exhausted and plan.requires_human
    assert plan.actions[0].action_type == RepairActionType.REQUEST_HUMAN_REVIEW
    assert not plan.actions[0].consumes_retry


def test_shot_design_failure_routes_to_director_replan() -> None:
    issue = EvaluationIssue(
        issue_type=EvaluationIssueType.WEAK_STORYTELLING,
        severity=IssueSeverity.MAJOR,
        message="Beat cannot read in this shot design",
        shot_id="shot_2",
    )
    evaluation = Evaluation(
        layer=EvaluationLayer.CINEMATIC,
        target_shot_id="shot_2",
        score=0.4,
        passed=False,
        issues=[issue],
    )
    plan = RepairPlanner().plan(evaluation, retry_budget=0, retry_count=0)

    assert IssueClassifier().classify(issue) == FailureClass.SHOT_DESIGN
    assert plan.actions[0].action_type == RepairActionType.DIRECTOR_REPLAN
    assert not plan.requires_human
