"""Deterministic technical checks that never require an AI model."""

from __future__ import annotations

from movie_agent.domain import (
    Artifact,
    ArtifactType,
    Evaluation,
    EvaluationIssue,
    EvaluationIssueType,
    EvaluationLayer,
    IssueSeverity,
    RepairActionType,
    Shot,
)


class TechnicalQC:
    """Validate type, URI, duration, dimensions, and basic media metadata."""

    def __init__(self, duration_tolerance: float = 0.15) -> None:
        if not 0 <= duration_tolerance <= 1:
            raise ValueError("duration_tolerance must be between zero and one")
        self.duration_tolerance = duration_tolerance

    def evaluate(self, shot: Shot, artifact: Artifact) -> Evaluation:
        issues: list[EvaluationIssue] = []
        if artifact.artifact_type != ArtifactType.VIDEO:
            issues.append(self._issue(shot, artifact, "Artifact is not video output."))
        if not artifact.uri:
            issues.append(self._issue(shot, artifact, "Artifact URI is empty."))

        duration = artifact.metadata.get("duration_seconds")
        if not isinstance(duration, (int, float)) or duration <= 0:
            issues.append(self._issue(shot, artifact, "Duration metadata is missing or invalid."))
        else:
            delta = abs(float(duration) - shot.duration_seconds) / shot.duration_seconds
            if delta > self.duration_tolerance:
                issues.append(
                    self._issue(
                        shot,
                        artifact,
                        f"Duration differs from the shot by {delta:.1%}.",
                    )
                )

        for dimension in ("width", "height"):
            value = artifact.metadata.get(dimension)
            if not isinstance(value, int) or value <= 0:
                issues.append(
                    self._issue(shot, artifact, f"{dimension} metadata is missing or invalid.")
                )

        passed = not issues
        return Evaluation(
            layer=EvaluationLayer.TECHNICAL_QC,
            target_artifact_id=artifact.artifact_id,
            target_artifact_version=artifact.version,
            target_shot_id=shot.shot_id,
            score=1.0 if passed else max(0.0, 1.0 - 0.2 * len(issues)),
            passed=passed,
            issues=issues,
            summary="Technical media contract passed." if passed else "Technical media contract failed.",
        )

    @staticmethod
    def _issue(shot: Shot, artifact: Artifact, message: str) -> EvaluationIssue:
        return EvaluationIssue(
            issue_type=EvaluationIssueType.GENERATION_FAILURE,
            severity=IssueSeverity.MAJOR,
            message=message,
            evidence=[artifact.uri],
            suggested_action=RepairActionType.REGENERATE,
            shot_id=shot.shot_id,
            artifact_id=artifact.artifact_id,
        )
