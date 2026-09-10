from unittest.mock import AsyncMock
import pytest
from movie_agent.domain import HumanGateType,WorkflowNodeStatus as S,ProviderErrorType
from movie_agent.providers.base import ProviderFailure
from movie_agent.model_services.coordinator import ResourceAdmissionWait
from movie_agent.orchestration.human_gate import HumanGateManager
from movie_agent.execution.events import LocalEventBus
from tests.test_p5r_progress import engine
from tests.test_resource_runtime import environment,job,success


@pytest.mark.asyncio
async def test_control_failure_before_submission_is_resource_admission_wait(tmp_path):
    runtime,_=environment(tmp_path)
    runtime.acquire=AsyncMock(side_effect=ProviderFailure('control error',ProviderErrorType.UNAVAILABLE))
    operation=AsyncMock()
    with pytest.raises(ResourceAdmissionWait):await runtime.execute(job(),operation)
    operation.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('recovers',[True,False])
async def test_admission_retry_is_bounded_and_does_not_omit_content(tmp_path,recovers):
    e=engine(tmp_path);plan=e.recovery_plan();plan.resource_retry_delay_seconds=0
    e.configure_recovery(plan);e.install_recovery()
    e.human_gates=HumanGateManager(LocalEventBus(),'test')
    e.block_shot=lambda *args:pytest.fail('Infrastructure failure must not mark shot content rejected')
    attempts=[]
    async def work(*args):
        attempts.append(1)
        if not recovers or len(attempts)==1:raise ResourceAdmissionWait('service is busy or draining')
    e.prepare_frames=work
    node='p5r_frames:'+e.current_project.shots[0].shot_id
    result=await e._execute_node(node,e.current_project,e.current_production,auto_approve=False)
    assert result is recovers and len(attempts)==(2 if recovers else 3)
    assert e.human_gates.all()==[]
    assert e.media_runtime.quality_ledger.records()==[]
    if not recovers:assert e.current_production.node(node).status==S.PENDING


def test_legacy_runtime_review_reclassified_without_touching_material_or_human_approval(tmp_path):
    e=engine(tmp_path);e.install_recovery();e.human_gates=HumanGateManager(LocalEventBus(),'test')
    node='p5r_frames:'+e.current_project.shots[0].shot_id
    e.current_production.set_status(node,S.WAITING_HUMAN)
    original=e.commit_quality('old_failure',{'node_id':node,'reason':'Spark control command failed'},'recovery_pending')
    review=e.human_gates.request(e.current_project.project_id,node,HumanGateType.AGENT_ESCALATION,'Spark control command failed')
    material=e.human_gates.request(e.current_project.project_id,'shot_gate',HumanGateType.AGENT_ESCALATION,'Missing visible prop')
    final=e.human_gates.request(e.current_project.project_id,'final_gate',HumanGateType.FINAL_CUT_APPROVAL,'Approve film?')
    assert e.reclassify_resource_waits()==[node]
    assert e.reclassify_resource_waits()==[]
    assert e.human_gates.get(review.review_id).status.value=='pending'
    assert e.human_gates.get(review.review_id).superseded_at is not None
    assert e.human_gates.get(material.review_id).superseded_at is None
    assert e.human_gates.get(final.review_id).status.value=='pending'
    assert e.artifact_store.get(original.artifact_id,original.version).metadata['sha256']==original.metadata['sha256']


@pytest.mark.asyncio
async def test_provider_execution_error_never_becomes_content_omission(tmp_path):
    e=engine(tmp_path);e.install_recovery();e.human_gates=HumanGateManager(LocalEventBus(),'test')
    e.block_shot=lambda *args:pytest.fail('Provider exception is not visual evidence')
    e.prepare_frames=AsyncMock(side_effect=ProviderFailure('REMOTE_COMPLETION_UNCERTAIN',ProviderErrorType.REMOTE_COMPLETION_UNCERTAIN))
    shot=e.current_project.shots[0];node='p5r_frames:'+shot.shot_id
    assert not await e._execute_node(node,e.current_project,e.current_production,auto_approve=False)
    record=e.artifact_store.read_structured(e.artifact_store.get('recovery_pending_'+shot.shot_id))
    assert record['classification']=='system_execution' and record['disposition']=='human_review'
    assert e.current_production.node(node).status==S.WAITING_HUMAN
    assert e.media_runtime.quality_ledger.records()==[]
