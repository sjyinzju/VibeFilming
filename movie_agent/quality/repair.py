"""Issue classification, repair policy, and bounded plan construction."""

from __future__ import annotations

from enum import StrEnum

from movie_agent.domain import (
    Evaluation,
    EvaluationIssue,
    EvaluationIssueType,
    RepairAction,
    RepairActionType,
    RepairPlan,
)


class FailureClass(StrEnum):
    """Broad failure origin used to distinguish regeneration from replanning."""

    GENERATION = "generation_failure"
    SHOT_DESIGN = "shot_design_failure"


class IssueClassifier:
    """Classify issues using explicit issue vocabulary rather than free text."""

    SHOT_DESIGN_ISSUES = {
        EvaluationIssueType.WEAK_STORYTELLING,
        EvaluationIssueType.CONTINUITY_CONFLICT,
        EvaluationIssueType.SHOT_DESIGN_FAILURE,
    }

    def classify(self, issue: EvaluationIssue) -> FailureClass:
        if issue.issue_type in self.SHOT_DESIGN_ISSUES:
            return FailureClass.SHOT_DESIGN
        return FailureClass.GENERATION


class RepairPolicy:
    """Map structured issues to finite actions and escalation rules."""

    ACTIONS: dict[EvaluationIssueType, RepairActionType] = {
        EvaluationIssueType.IDENTITY_DRIFT: RepairActionType.STRENGTHEN_CHARACTER_REFERENCE,
        EvaluationIssueType.SCENE_DRIFT: RepairActionType.STRENGTHEN_SCENE_REFERENCE,
        EvaluationIssueType.MISSING_DETAIL: RepairActionType.REWRITE_PROMPT,
        EvaluationIssueType.HALLUCINATED_OBJECT: RepairActionType.REWRITE_PROMPT,
        EvaluationIssueType.EXTRA_CHARACTER: RepairActionType.REWRITE_PROMPT,
        EvaluationIssueType.ACTION_FAILURE: RepairActionType.REWRITE_PROMPT,
        EvaluationIssueType.CAMERA_MOTION_MISMATCH: RepairActionType.CHANGE_GENERATION_STRATEGY,
        EvaluationIssueType.CONTINUITY_ERROR: RepairActionType.REGENERATE_FIRST_FRAME,
        EvaluationIssueType.CONTINUITY_CONFLICT: RepairActionType.DIRECTOR_REPLAN,
        EvaluationIssueType.VISUAL_ARTIFACT: RepairActionType.REGENERATE,
        EvaluationIssueType.WEAK_STORYTELLING: RepairActionType.DIRECTOR_REPLAN,
        EvaluationIssueType.STYLE_MISMATCH: RepairActionType.STRENGTHEN_SCENE_REFERENCE,
        EvaluationIssueType.GENERATION_FAILURE: RepairActionType.REGENERATE,
        EvaluationIssueType.SHOT_DESIGN_FAILURE: RepairActionType.DIRECTOR_REPLAN,
    }

    def action_for(self, issue: EvaluationIssue) -> RepairActionType:
        return issue.suggested_action or self.ACTIONS[issue.issue_type]


class RepairPlanner:
    """Build a plan, enforcing the retry budget before another generation attempt."""

    def __init__(
        self,
        policy: RepairPolicy | None = None,
        classifier: IssueClassifier | None = None,
    ) -> None:
        self.policy = policy or RepairPolicy()
        self.classifier = classifier or IssueClassifier()

    def plan_media(self, inspection, *, retry_budget, retry_count, supported_actions, directive=None):
        from movie_agent.media import (MediaRepairPlan, MediaRepairAction, MediaRepairActionType as A,
                                      RepairContext, HumanRepairDisposition as D)
        from movie_agent.quality.vision import BLOCKING
        dismissed = set(directive.dismissed_issue_ids) if directive else set()
        accepted = set(directive.accepted_issue_ids) if directive else set()
        issues = [i for i in inspection.issues if i.issue_id not in dismissed
                  and (i.issue_id in accepted if accepted else i.severity in BLOCKING)]
        actions = [MediaRepairAction(action_type=i.suggested_action or A.REQUEST_HUMAN_REVIEW,
            issue_ids=[i.issue_id], target_artifact_id=inspection.target_artifact_id,
            target_artifact_version=inspection.target_artifact_version, target_shot_id=inspection.shot_id,
            rationale=i.message) for i in issues]
        if directive and directive.disposition == D.REGENERATE:
            actions = [MediaRepairAction(action_type=A.REGENERATE_VIDEO,
                target_artifact_id=inspection.target_artifact_id,
                target_artifact_version=inspection.target_artifact_version, target_shot_id=inspection.shot_id,
                rationale="Human requested a new generation of the same canonical Shot")]
            issues = []
        elif directive and directive.disposition == D.CUSTOM_REPAIR:
            actions.append(MediaRepairAction(action_type=A.REWRITE_PROMPT,
                target_artifact_id=inspection.target_artifact_id,
                target_artifact_version=inspection.target_artifact_version, target_shot_id=inspection.shot_id,
                rationale="Apply human media feedback while preserving canonical Shot intent"))
        unsupported = list(dict.fromkeys(a.action_type for a in actions if a.action_type not in supported_actions))
        # A human directive authorizes one explicit additional media attempt. The
        # subsequent automatic loop still uses the original Shot retry budget.
        effective_budget = max(retry_budget, retry_count + 1) if directive else retry_budget
        exhausted = retry_count >= effective_budget
        fixes = []
        evidence = []
        for issue in issues:
            ranges = ", ".join(f"{t.start_seconds:g}–{t.end_seconds:g}s" for t in issue.time_ranges)
            fixes.append(f"{issue.issue_type.value} {ranges}: {issue.message}")
            evidence.extend(issue.evidence)
        if directive:
            fixes.extend(directive.change_requests)
        if not fixes:
            fixes = ["Regenerate the media to faithfully execute the unchanged canonical Shot."]
        context = RepairContext(target_artifact_id=inspection.target_artifact_id,
            target_artifact_version=inspection.target_artifact_version, target_sha256=inspection.target_sha256,
            inspection_result_id=inspection.result_id, directive_id=directive.directive_id if directive else None,
            preserve=["Canonical story, Shot action, character identities, clothing, scene and composition",
                      *(directive.preserve_requirements if directive else [])],
            fix=fixes, avoid=["Do not add characters, props, story events or change canonical Shot intent"],
            evidence=evidence, targeted_time_ranges=[t for issue in issues for t in issue.time_ranges],
            raw_feedback=directive.feedback if directive else "")
        return MediaRepairPlan(inspection_result_id=inspection.result_id, actions=actions,
            retry_budget=effective_budget, retry_count=retry_count, exhausted=exhausted,
            requires_human=exhausted or bool(unsupported) or not actions,
            directive_id=directive.directive_id if directive else None, repair_context=context,
            unsupported_actions=unsupported)

    def plan(
        self,
        evaluation: Evaluation,
        *,
        retry_budget: int,
        retry_count: int,
    ) -> RepairPlan:
        if evaluation.passed:
            return RepairPlan(
                evaluation_id=evaluation.evaluation_id,
                retry_budget=retry_budget,
                retry_count=retry_count,
            )

        exhausted = retry_count >= retry_budget
        contains_design_failure = any(
            self.classifier.classify(issue) == FailureClass.SHOT_DESIGN
            for issue in evaluation.issues
        )
        if exhausted:
            action_type = (
                RepairActionType.DIRECTOR_REPLAN
                if contains_design_failure
                else RepairActionType.REQUEST_HUMAN_REVIEW
            )
            action = RepairAction(
                action_type=action_type,
                issue_ids=[issue.issue_id for issue in evaluation.issues],
                target_shot_id=evaluation.target_shot_id,
                target_artifact_id=evaluation.target_artifact_id,
                rationale="Retry budget exceeded; escalate instead of retrying indefinitely.",
                consumes_retry=False,
            )
            return RepairPlan(
                evaluation_id=evaluation.evaluation_id,
                actions=[action],
                retry_budget=retry_budget,
                retry_count=retry_count,
                exhausted=True,
                requires_human=action_type == RepairActionType.REQUEST_HUMAN_REVIEW,
            )

        actions = [
            RepairAction(
                action_type=self.policy.action_for(issue),
                issue_ids=[issue.issue_id],
                target_shot_id=issue.shot_id or evaluation.target_shot_id,
                target_artifact_id=issue.artifact_id or evaluation.target_artifact_id,
                rationale=f"Route structured issue {issue.issue_type.value} through repair policy.",
                consumes_retry=True,
            )
            for issue in evaluation.issues
        ]
        return RepairPlan(
            evaluation_id=evaluation.evaluation_id,
            actions=actions,
            retry_budget=retry_budget,
            retry_count=retry_count,
            exhausted=False,
            requires_human=False,
        )
