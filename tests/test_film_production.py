import pytest
from movie_agent.application.production_factory import production_engine
from movie_agent.application.service import ProductionService
from movie_agent.storage.projects import LocalProjectRepository
from movie_agent.services.film_production import FilmProduction
from movie_agent.services.reasoning_production import ReasoningMovieProduction
from movie_agent.quality.production import FilmProductionPolicy,plan_references
from movie_agent.domain import Project,ProjectBrief,Character,Location,Scene,Prop,PropState
from tests.p2a_fakes import FakeReasoningProvider
from tests.test_p2a_runtime import brief
from tests.test_p5_quality import shot


@pytest.mark.parametrize('identity',['botanist','mariner'])
def test_reference_plan_uses_arbitrary_canonical_subjects_and_visible_props(identity):
    s=shot();state=next(iter(s.state_before.character_states.values()))
    s.state_before.character_states={identity:state.model_copy(update={'character_id':identity})}
    s.state_before.prop_states={'tool_'+identity:PropState(prop_id='tool_'+identity)}
    from movie_agent.domain import VisualBible
    project=Project(brief=brief(),shots=[s],characters=[Character(character_id=identity,name=identity)],
        locations=[Location(location_id='place_'+identity,name='Harbor')],props=[Prop(prop_id='tool_'+identity,name='Compass')],
        scenes=[Scene(scene_id=s.scene_id,title='Arrival',purpose='Choice',location_id='place_'+identity,time_description='day')],
        visual_bible=VisualBible(style_statement='natural'))
    plan=plan_references(project,FilmProductionPolicy())
    assert set(plan.reference_gate_nodes)>={identity,'tool_'+identity,'place_'+identity}
    assert plan.candidate.minimum_shots==1 and plan.candidate.minimum_seconds==pytest.approx(9.6)
    assert plan.candidate.export_before_optional_work is False
    assert next(a for a in plan.reference_actions if a.subject_id==identity).instruction.find(identity)>=0


@pytest.mark.asyncio
async def test_formal_application_creates_and_restores_quality_dag_without_fixture_script(tmp_path):
    provider=FakeReasoningProvider()
    service=ProductionService(LocalProjectRepository(tmp_path),lambda pid:production_engine(tmp_path/pid,provider))
    record=service.create(brief());engine=service.engine(record.project.project_id)
    assert isinstance(engine,FilmProduction)
    engine.initialize_project(engine.current_project,engine.current_production,FilmProductionPolicy(require_real_providers=False))
    result=await engine.run(resume=True,stop_after_node='asset_planning')
    assert result.completed is False
    plan=engine.recovery_plan()
    assert plan.entry_node_id=='asset_planning' and plan.reference_actions
    assert all(engine.current_production.node('production_frames:'+s.shot_id) for s in engine.current_project.shots)
    assert not any(r.gate_type.value in {'story_approval','shot_plan_approval'} for r in engine.human_gates.all())
    restored=production_engine(tmp_path/record.project.project_id,provider)
    assert isinstance(restored,FilmProduction)
    restored._restore()
    assert restored.thresholds.profile==record.project.brief.quality_level


@pytest.mark.asyncio
async def test_formal_production_fails_closed_when_only_mock_media_are_configured(tmp_path):
    provider=FakeReasoningProvider()
    service=ProductionService(LocalProjectRepository(tmp_path),lambda pid:production_engine(tmp_path/pid,provider))
    record=service.create(brief());service.start(record.project.project_id)
    await service.tasks[record.project.project_id]
    engine=service.engine(record.project.project_id)
    assert engine.current_production.node('brief').status.value=='waiting_human'
    assert provider.calls==[]
    assert engine.artifact_store.get('production_readiness') is not None


def test_factory_preserves_legacy_project_engine(tmp_path):
    provider=FakeReasoningProvider()
    service=ProductionService(LocalProjectRepository(tmp_path),lambda pid:ReasoningMovieProduction(tmp_path/pid,provider))
    record=service.create(brief())
    assert type(production_engine(tmp_path/record.project.project_id,provider)) is ReasoningMovieProduction


@pytest.mark.asyncio
async def test_api_persists_explicit_project_policy_and_compute_budget(tmp_path):
    import httpx
    from movie_agent.api.app import create_app
    provider=FakeReasoningProvider()
    service=ProductionService(LocalProjectRepository(tmp_path),lambda pid:production_engine(tmp_path/pid,provider))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service)),base_url='http://test') as client:
        response=await client.post('/projects',json={**brief().model_dump(mode='json'),'production_policy':{
            'require_real_providers':False,'minimum_candidate_coverage':1,'quality_budget':{'h3_total':40}}})
    assert response.status_code==201,response.text
    pid=response.json()['project']['project_id'];engine=service.engine(pid)
    await engine.run(resume=True,stop_after_node='asset_planning')
    assert engine.recovery_plan().candidate.minimum_seconds==12
    assert engine.media_runtime.quality_ledger.budget.h3_total==40
    restored=production_engine(tmp_path/pid,provider);restored._restore()
    assert restored.media_runtime.quality_ledger.budget.h3_total==40
    from movie_agent.quality.budget import QualityBudget
    assert QualityBudget().h3_total==18  # Existing fixtures keep their original cap.
