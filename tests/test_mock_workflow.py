"""End-to-end proof of the complete, model-free film production lifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path

from movie_agent.domain import (
    ArtifactType,
    EvaluationLayer,
    EventType,
    JobStatus,
    RepairActionType,
    ReviewStatus,
    WorkflowNodeStatus,
)
from movie_agent.services import MockMovieProduction

FIXTURE = Path(__file__).parent / "fixtures" / "sample_brief.json"


def test_complete_mock_production_repairs_and_preserves_versions(tmp_path: Path) -> None:
    production = MockMovieProduction(tmp_path / "complete")
    brief = production.load_brief(FIXTURE)

    result = asyncio.run(production.run(brief))

    assert result.completed is True
    assert result.final_artifact_id == "final_film"
    assert all(node.status == WorkflowNodeStatus.SUCCEEDED for node in result.workflow_graph.nodes)
    assert all(job.status == JobStatus.SUCCEEDED for job in result.jobs)
    assert len([item for item in result.artifacts if item.artifact_type == ArtifactType.FRAME]) == 6

    versions = production.artifact_store.list_versions("video_shot_002")
    assert [item.version for item in versions] == [1, 2]
    assert versions[0].uri != versions[1].uri
    assert versions[0].selected is False
    assert versions[1].selected is True
    assert Path(versions[0].uri.removeprefix("file:///")) != Path(
        versions[1].uri.removeprefix("file:///")
    )

    first_failure = next(
        evaluation
        for evaluation in result.evaluations
        if evaluation.layer == EvaluationLayer.VISUAL_SEMANTIC
        and evaluation.target_shot_id == "shot_002"
        and not evaluation.passed
    )
    assert any(issue.suggested_action == RepairActionType.REWRITE_PROMPT for issue in first_failure.issues)
    assert any(
        evaluation.layer == EvaluationLayer.VISUAL_SEMANTIC
        and evaluation.target_shot_id == "shot_002"
        and evaluation.passed
        for evaluation in result.evaluations
    )
    assert len(result.repair_plans) == 1
    assert result.repair_plans[0].actions[0].action_type == RepairActionType.REWRITE_PROMPT

    repaired = versions[1]
    assert repaired.provenance.provider_id == "mock-video"
    assert repaired.provenance.prompt_package_id
    assert repaired.provenance.generation_strategy
    assert repaired.provenance.repair_plan_ids == [result.repair_plans[0].repair_plan_id]
    assert result.latest_checkpoint_id
    assert EventType.WORKFLOW_COMPLETED in [event.event_type for event in result.event_stream]


def test_checkpoint_resume_skips_completed_nodes_and_finishes(tmp_path: Path) -> None:
    workspace = tmp_path / "resume"
    first_run = MockMovieProduction(workspace)
    brief = first_run.load_brief(FIXTURE)

    stopped = asyncio.run(first_run.run(brief, stop_after_node="storyboard_planning"))
    assert stopped.completed is False
    assert stopped.latest_checkpoint_id
    assert next(
        node for node in stopped.workflow_graph.nodes if node.node_id == "storyboard_planning"
    ).status == WorkflowNodeStatus.SUCCEEDED
    frame_uris = {
        artifact.uri for artifact in stopped.artifacts if artifact.artifact_type == ArtifactType.FRAME
    }

    resumed_run = MockMovieProduction(workspace)
    resumed = asyncio.run(resumed_run.run(resume=True))

    assert resumed.completed is True
    assert resumed.project.project_id == stopped.project.project_id
    assert frame_uris == {
        artifact.uri for artifact in resumed.artifacts if artifact.artifact_type == ArtifactType.FRAME
    }
    assert len(resumed.human_reviews) == 3
    assert resumed.final_artifact_id == "final_film"


def test_full_workflow_pauses_and_resumes_the_same_human_gate(tmp_path: Path) -> None:
    workspace = tmp_path / "human-resume"
    first_run = MockMovieProduction(workspace)
    paused = asyncio.run(first_run.run(first_run.load_brief(FIXTURE), auto_approve=False))

    assert paused.completed is False
    assert len(paused.human_reviews) == 1
    review_id = paused.human_reviews[0].review_id
    assert paused.human_reviews[0].status == ReviewStatus.PENDING
    story_gate = next(node for node in paused.workflow_graph.nodes if node.node_id == "story_gate")
    assert story_gate.status == WorkflowNodeStatus.WAITING_HUMAN

    resumed = asyncio.run(MockMovieProduction(workspace).run(resume=True))

    restored_review = next(item for item in resumed.human_reviews if item.review_id == review_id)
    assert restored_review.status == ReviewStatus.APPROVED
    assert len(resumed.human_reviews) == 3
    assert resumed.completed is True
