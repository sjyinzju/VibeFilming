from types import SimpleNamespace as NS
import pytest
from movie_agent.domain import HumanGateType,WorkflowNodeStatus as S
from movie_agent.execution.events import LocalEventBus
from movie_agent.orchestration.human_gate import HumanGateManager
from movie_agent.services.recovery_authorization import configure_quality_authorization
from tests.test_p5r_progress import engine


@pytest.mark.parametrize('outcome,finish,upgrade,authorized,changes',[
    ('critic_invalid_or_transport','stop',True,True,True),
    ('critic_invalid_or_transport','length',True,True,False),
    ('material_rejected','stop',True,True,False),
    ('critic_invalid_or_transport','stop',False,True,False),
    ('critic_invalid_or_transport','stop',True,False,False)])
@pytest.mark.parametrize('diagnostic',['VLM structured/semantic output invalid after bounded repair','GENERATION_FAILED'])
def test_only_authorized_completed_invalid_reviews_requeue_without_budget_reset(tmp_path,outcome,finish,upgrade,authorized,changes,diagnostic):
    e=engine(tmp_path);e.install_recovery();e.human_gates=HumanGateManager(LocalEventBus(),'test')
    artifact=e.commit_quality('environment',{'source':'exact reference'},'test_source')
    e.pin=lambda a:NS(artifact_id=a.artifact_id,version=a.version,sha256=a.metadata['sha256'])
    node=e.recovery_plan().reference_actions[0].node_id
    review=e.human_gates.request(e.current_project.project_id,node,HumanGateType.AGENT_ESCALATION,
        diagnostic)
    final=e.human_gates.request(e.current_project.project_id,'final_gate',HumanGateType.FINAL_CUT_APPROVAL,'Approve film?')
    e.current_production.set_status(node,S.WAITING_HUMAN)
    evidence={'subject':artifact.artifact_id,'version':artifact.version,'sha256':artifact.metadata['sha256'],
        'outcome':outcome,'quality_policy_revision':'policy','critic_config_revision':'old',
        'provider_attempts':[{'finish_reason':finish,'rejected_proposal':{'issues':['Original negative observation']}}]}
    original=e.commit_quality('p5r_critic_outcome',evidence,'critic_budget_outcome')
    records=e.media_runtime.quality_ledger.critic_outcomes()
    auth={'policy_revisions':['policy'],'critic_config_revision':'new' if upgrade else 'old',
        'supersede_invalid_critic_reviews':authorized}
    configure_quality_authorization(e,auth,user_statement='Explicitly allow technical review supersession.')
    assert (e.human_gates.get(review.review_id).superseded_at is not None)==changes
    assert e.current_production.node(node).status==(S.PENDING if changes else S.WAITING_HUMAN)
    assert e.human_gates.get(review.review_id).status.value=='pending'
    assert e.human_gates.get(final.review_id).superseded_at is None
    assert e.media_runtime.quality_ledger.critic_outcomes()==records
    assert e.artifact_store.read_structured(original)==evidence
    configure_quality_authorization(e,auth,user_statement='Explicitly allow technical review supersession.')
    assert len(e.artifact_store.list_versions('critic_supersession_'+review.review_id))==int(changes)
