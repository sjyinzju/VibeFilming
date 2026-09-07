"""Audited request-contract type revision over a preserved failed scene."""

import asyncio
import pytest

from movie_agent.domain import (
    CameraSpec, LightingSpec, ShotNarrative, ShotSize, WorkflowNodeStatus,
)
from movie_agent.orchestration.runtime.cinematographer_drafts import (
    FramePlanningDraft, MAPPER_VERSION, ShotDraft, ShotLocalStateDraft, ShotPlanDraft,
)
from movie_agent.orchestration.runtime.context import content_hash
from movie_agent.orchestration.runtime.runner import RoleOutputInvalid
from movie_agent.services.reasoning_production import ReasoningMovieProduction
from tests.p2a_fakes import FakeReasoningProvider
from tests.test_p2a_runtime import brief


def prepare_scope_failure(workspace):
    """Build a durable analogue of the preserved real Scene-2 scope failure."""
    production = ReasoningMovieProduction(workspace, FakeReasoningProvider())
    asyncio.run(production.run(brief(), stop_after_node="shot_planning"))
    key = "cinematographer:scene_a"
    failed = production.role_results[key]
    failed.committed = False
    failed.failure_code = "CONTRACT_STATE_SCOPE_CONFLICT"
    production.current_project.shots = []
    production.current_project.continuity_chains = []
    production.current_project.scenes[0].shot_ids = []
    production.current_production.node("shot_planning").status = WorkflowNodeStatus.FAILED
    production._save_checkpoint(production.current_project, production.current_production)
    return production


def test_type_revision_preserves_parent_and_resumes_with_draft_only(tmp_path):
    production = prepare_scope_failure(tmp_path)
    key = "cinematographer:scene_a"
    prior = production.role_results[key].model_dump(mode="json")
    earlier = [item.model_dump(mode="json") for item in production.role_revision_history]
    passed = {k: content_hash(v.model_dump(mode="json")) for k, v in production.role_results.items()
              if k != key}
    production.authorize_request_contract_type_revision("scene_a", "final completion authorization")
    current = production.role_results[key]
    revision = current.invocation.request_contract_revision
    assert revision.kind == "REQUEST_CONTRACT_TYPE_REVISION"
    assert revision.parent_result_hash == content_hash(prior)
    assert revision.old_schema_hash != revision.new_schema_hash
    assert revision.mapper_version == MAPPER_VERSION
    assert [item.model_dump(mode="json") for item in production.role_revision_history] == [*earlier, prior]
    assert {k: content_hash(v.model_dump(mode="json")) for k, v in production.role_results.items()
            if k != key} == passed
    restarted = ReasoningMovieProduction(tmp_path, FakeReasoningProvider())
    assert restarted._restore()[0].shots == []
    assert restarted.request_contract_type_revisions[0] == revision
    result = asyncio.run(restarted.run(resume=True))
    assert result.completed and restarted.llm_provider.calls == ["ShotPlanDraft"]
    assert restarted.role_results[key].committed
    assert restarted.role_revision_history[-1].model_dump(mode="json") == prior
    artifact = restarted.artifact_store.get("request_contract_type_revision_scene_a")
    assert artifact.provenance.tool == "REQUEST_CONTRACT_TYPE_REVISION"


def test_type_revision_is_single_use_and_keeps_two_repair_cap(tmp_path):
    class InvalidDraft(FakeReasoningProvider):
        async def submit(self, request):
            result = await super().submit(request)
            if self.calls[-1] == "ShotPlanDraft":
                result.metadata["content"] = "{}"
            return result
    production = prepare_scope_failure(tmp_path)
    production.authorize_request_contract_type_revision("scene_a", "final completion authorization")
    with pytest.raises(ValueError, match="already been consumed"):
        production.authorize_request_contract_type_revision("scene_a", "again")
    restarted = ReasoningMovieProduction(tmp_path, InvalidDraft())
    with pytest.raises(RoleOutputInvalid):
        asyncio.run(restarted.run(resume=True))
    assert restarted.llm_provider.calls == ["ShotPlanDraft"] * 3
    with pytest.raises(RoleOutputInvalid):
        asyncio.run(restarted.run(resume=True))
    assert restarted.llm_provider.calls == ["ShotPlanDraft"] * 3


def test_schema_lineage_tamper_is_rejected_before_provider_call(tmp_path):
    production = prepare_scope_failure(tmp_path)
    production.authorize_request_contract_type_revision("scene_a", "final completion authorization")
    production.role_results["cinematographer:scene_a"].invocation.request_contract_revision.new_schema_hash = "tampered"
    production._save_checkpoint(production.current_project, production.current_production)
    restarted = ReasoningMovieProduction(tmp_path, FakeReasoningProvider())
    with pytest.raises(ValueError, match="contract type changed"):
        asyncio.run(restarted.run(resume=True))
    assert restarted.llm_provider.calls == []


def test_exhausted_output_can_be_revalidated_after_code_fix_without_inference(tmp_path):
    class InvalidDraft(FakeReasoningProvider):
        async def submit(self, request):
            result = await super().submit(request)
            if self.calls[-1] == "ShotPlanDraft":
                result.metadata["content"] = "{}"
            return result

    production = prepare_scope_failure(tmp_path)
    production.authorize_request_contract_type_revision("scene_a", "final completion authorization")
    exhausted = ReasoningMovieProduction(tmp_path, InvalidDraft())
    with pytest.raises(RoleOutputInvalid):
        asyncio.run(exhausted.run(resume=True))
    saved = exhausted.role_results["cinematographer:scene_a"]
    original_attempts = [attempt.model_dump(mode="json") for attempt in saved.attempts]
    project = exhausted.current_project
    saved.pending_output = ShotPlanDraft(
        preserved_constraints=project.brief.user_constraints + project.brief.must_preserve,
        shots=[ShotDraft(duration_seconds=project.brief.target_duration,
            narrative=ShotNarrative(purpose="Listen", beat="The signal stops"),
            camera=CameraSpec(shot_size=ShotSize.WIDE),
            lighting=LightingSpec(setup="practical"),
            frame_planning=FramePlanningDraft(first_frame_description="open",
                last_frame_description="close"),
            local_state_delta=ShotLocalStateDraft())]).model_dump_json()
    exhausted._save_checkpoint(exhausted.current_project, exhausted.current_production)

    replayed = ReasoningMovieProduction(tmp_path, FakeReasoningProvider())
    result = asyncio.run(replayed.run(resume=True))

    role_result = replayed.role_results["cinematographer:scene_a"]
    assert result.completed and replayed.llm_provider.calls == []
    assert [attempt.model_dump(mode="json") for attempt in role_result.attempts] == original_attempts
    assert role_result.validation_replays[-1].validation.valid
    assert role_result.validation_replays[-1].mapper_version == MAPPER_VERSION
