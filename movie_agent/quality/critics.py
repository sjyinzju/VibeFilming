"""Critic interfaces plus deterministic mocks for model-free production tests."""

from __future__ import annotations

from typing import Protocol

from movie_agent.domain import (
    Artifact,
    Evaluation,
    EvaluationIssue,
    EvaluationIssueType,
    EvaluationLayer,
    IssueSeverity,
    RepairActionType,
    Shot,
)


class Critic(Protocol):
    """Quality critic boundary that can later be backed by vision or LLM providers."""

    def evaluate(self, shot: Shot, artifact: Artifact) -> Evaluation: ...


class VisualSemanticCritic(Critic, Protocol):
    """Checks semantic execution, identity, scene, action, and visual defects."""


class CinematicCritic(Critic, Protocol):
    """Checks storytelling, pacing, style, composition, and camera intent."""


class MockVisualSemanticCritic:
    """Fail selected shot versions exactly once, then return a structured pass."""

    def __init__(self, fail_once_shot_ids: set[str] | None = None) -> None:
        self.fail_once_shot_ids = set(fail_once_shot_ids or set())
        self._failed: set[str] = set()

    def evaluate(self, shot: Shot, artifact: Artifact) -> Evaluation:
        should_fail = (
            shot.shot_id in self.fail_once_shot_ids
            and artifact.version == 1
            and shot.shot_id not in self._failed
        )
        if should_fail:
            self._failed.add(shot.shot_id)
            issue = EvaluationIssue(
                issue_type=EvaluationIssueType.ACTION_FAILURE,
                severity=IssueSeverity.MAJOR,
                message="Mock subject fails to complete the declared action.",
                evidence=[f"artifact={artifact.artifact_id}@{artifact.version}"],
                suggested_action=RepairActionType.REWRITE_PROMPT,
                shot_id=shot.shot_id,
                artifact_id=artifact.artifact_id,
            )
            return Evaluation(
                layer=EvaluationLayer.VISUAL_SEMANTIC,
                target_artifact_id=artifact.artifact_id,
                target_shot_id=shot.shot_id,
                score=0.45,
                passed=False,
                issues=[issue],
                summary="Intentional first-pass mock semantic failure.",
            )
        return Evaluation(
            layer=EvaluationLayer.VISUAL_SEMANTIC,
            target_artifact_id=artifact.artifact_id,
            target_shot_id=shot.shot_id,
            score=0.94,
            passed=True,
            summary="Mock semantic and visual checks passed.",
        )


class MockCinematicCritic:
    """Deterministic cinematic critic used by tests and the local demo."""

    def evaluate(self, shot: Shot, artifact: Artifact) -> Evaluation:
        return Evaluation(
            layer=EvaluationLayer.CINEMATIC,
            target_artifact_id=artifact.artifact_id,
            target_shot_id=shot.shot_id,
            score=0.91,
            passed=True,
            summary="Mock composition, camera intent, and storytelling checks passed.",
        )
