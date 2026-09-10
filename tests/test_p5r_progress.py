from types import SimpleNamespace as NS
from movie_agent.domain import Project, ProjectBrief, Scene, WorkflowNodeStatus as S
from movie_agent.artifacts import LocalArtifactStore
from movie_agent.quality.budget import QualityBudgetLedger
from movie_agent.services.reference_recovery import ReferenceRecoveryProduction
from movie_agent.orchestration.graph import ProductionGraph
from movie_agent.domain import WorkflowGraph, WorkflowNode
from tests.test_p5_quality import shot
import pytest


def engine(tmp_path):
    e=ReferenceRecoveryProduction.__new__(ReferenceRecoveryProduction)
    e.artifact_store=LocalArtifactStore(tmp_path)
    e.media_runtime=NS(quality_ledger=QualityBudgetLedger(e.artifact_store))
    e._durable_media_checkpoint=lambda:None
    e.commit_quality=lambda identity,content,purpose:e.artifact_store.create_structured(identity,content,metadata={'purpose':purpose})
    s=shot();s.shot_id='SCENE_001-SHOT-01';s.scene_id='scene';s.state_before.character_states={}
    e.current_project=Project(brief=ProjectBrief(title='Recovery',logline='Recovery test',target_duration=75,story_description='Test'),shots=[s],
        scenes=[Scene(scene_id='scene',title='scene',purpose='setup',location_id='LOC_SKY_CITY_001',time_description='day')])
    e.current_production=ProductionGraph(WorkflowGraph(project_id=e.current_project.project_id,nodes=[
        WorkflowNode(node_id=name,node_type='test',label=name,role='core',group='test',status=S.SUCCEEDED)
        for name in ('shot_gate','audio_post','rough_cut','full_film_review','final_gate','final_render')]))
    from movie_agent.quality.recovery import RecoveryPlan, ReferenceRecoveryAction
    plan=RecoveryPlan(plan_revision='test',namespace='p5r',reference_gate_nodes={'LOC_SKY_CITY_001':'p5r_reference:LOC_SKY_CITY_001'},
        reference_actions=[ReferenceRecoveryAction(node_id='p5r_reference:LOC_SKY_CITY_001',subject_id='LOC_SKY_CITY_001',
            reference_type='location',artifact_id='environment',purpose='location_plate',strategy='inspect_existing')])
    e.configure_recovery(plan)
    return e


def test_dag_shot_becomes_eligible_without_all_references(tmp_path):
    e=engine(tmp_path)
    from movie_agent.quality.recovery import ReferenceRecoveryAction
    plan=e.recovery_plan()
    plan.reference_actions.append(ReferenceRecoveryAction(node_id='unrelated_reference',subject_id='unrelated_person',
        reference_type='character',artifact_id='other_asset',purpose='character_plate',strategy='inspect_existing'))
    e.configure_recovery(plan);e.install_recovery();graph=e.current_production
    frame=graph.node('p5r_frames:SCENE_001-SHOT-01')
    assert frame.dependencies==['p5r_reference:LOC_SKY_CITY_001']
    graph.set_status('p5r_reference:LOC_SKY_CITY_001',S.SUCCEEDED)
    assert frame.node_id in {n.node_id for n in graph.ready_nodes()}
    assert graph.node('unrelated_reference').status==S.PENDING
    graph.set_status(frame.node_id,S.SUCCEEDED)
    graph.set_status('p5r_frame_gate:SCENE_001-SHOT-01',S.SUCCEEDED)
    assert 'p5r_video:SCENE_001-SHOT-01' in {n.node_id for n in graph.ready_nodes()}
    assert not e.node_eligible(graph.node('p5r_candidate'),e.current_project,graph)
    count=len(graph.graph.nodes);e.install_recovery();assert len(graph.graph.nodes)==count


def test_candidate_priority_preempts_optional_nodes_after_minimum(tmp_path):
    e=engine(tmp_path);e.install_recovery();g=e.current_production
    e.candidate_ready=lambda project:True
    assert e.node_eligible(g.node('p5r_candidate'),e.current_project,g)
    assert not e.node_eligible(g.node('p5r_reference:LOC_SKY_CITY_001'),e.current_project,g)
    assert e.node_eligible(g.node('audio_post'),e.current_project,g)


def test_resume_restores_configured_quality_policy_without_rewriting_it(tmp_path,monkeypatch):
    from movie_agent.services.hero_production import HeroMovieProduction
    from movie_agent.quality.reports import QualityThresholds
    e=engine(tmp_path);expected=e.thresholds.model_dump()
    version=e.artifact_store.get('active_quality_policy').version
    e.commit_quality('quality_recovery_authorization',{'critic_config_revision':'persisted-critic'},'recovery_authorization')
    e.thresholds=QualityThresholds()
    monkeypatch.setattr(HeroMovieProduction,'_restore_extra',lambda self,state:None)
    e._restore_extra({})
    assert e.thresholds.model_dump()==expected
    assert e.critic_revision=='persisted-critic'
    assert e.media_runtime.inspection_context['critic_config_revision']=='persisted-critic'
    assert e.artifact_store.get('active_quality_policy').version==version


@pytest.mark.asyncio
async def test_application_resume_preserves_unapproved_gate(tmp_path):
    from tests.test_p2a_api import service_at
    from tests.p2a_fakes import FakeReasoningProvider
    from tests.test_p2a_runtime import brief
    from movie_agent.application.service import CommandConflict
    service=service_at(tmp_path,FakeReasoningProvider())
    pid=service.create(brief()).project.project_id
    service.start(pid);await service.tasks[pid]
    engine=service.engine(pid)
    reviews=[r.model_dump(mode='json') for r in engine.human_gates.all()]
    with pytest.raises(CommandConflict):service.start(pid,resume=True)
    engine.continue_independent_on_review=True
    service.start(pid,resume=True);await service.tasks[pid]
    assert [r.model_dump(mode='json') for r in engine.human_gates.all()]==reviews
    assert engine.current_production.node('story_gate').status==S.WAITING_HUMAN
