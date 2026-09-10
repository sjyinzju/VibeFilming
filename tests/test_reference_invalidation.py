from movie_agent.domain import CharacterState,WorkflowNode,WorkflowNodeStatus as S,HumanGateType
from movie_agent.media import MediaReference
from movie_agent.application.reference_invalidation import invalidate_reference_dependents
from movie_agent.orchestration.human_gate import HumanGateManager
from movie_agent.execution.events import LocalEventBus
from tests.test_p5r_progress import engine


def test_reference_change_invalidates_visual_consumers_and_post_but_preserves_audio_and_history(tmp_path):
    e=engine(tmp_path);first=e.current_project.shots[0]
    first.state_before.character_states={'first_person':CharacterState(character_id='first_person')}
    other=first.model_copy(deep=True,update={'shot_id':'unrelated_shot'})
    other.state_before.character_states={'second_person':CharacterState(character_id='second_person')}
    e.current_project.shots.append(other)
    plan=e.recovery_plan();plan.reference_gate_nodes.update({'first_person':'shot_gate','second_person':'shot_gate'})
    e.configure_recovery(plan);e.install_recovery()
    e.human_gates=HumanGateManager(LocalEventBus(),'trace')
    old_review=e.human_gates.request(e.current_project.project_id,'final_gate',HumanGateType.FINAL_CUT_APPROVAL,'old candidate')
    e.current_production.add_node(WorkflowNode(node_id='audio_voice:first_person',node_type='voice_design',label='Voice',
        role='Sound',group='audio',status=S.SUCCEEDED))
    store=e.artifact_store
    old=store.create_structured('frame_gate_'+first.shot_id,{'passed':True,'frames':[]})
    store.create_structured('frame_gate_'+other.shot_id,{'passed':True,'frames':[]})
    image=store.create_structured('new_face',{})
    refs=[MediaReference(reference_type='character',artifact_id=image.artifact_id,version=1,sha256=image.metadata['sha256'])]
    e.shot_references=lambda *args:refs
    refreshed=[];e.reports=lambda project:refreshed.append(project.project_id)
    for node in e.current_production.graph.nodes:node.status=S.SUCCEEDED
    invalidate_reference_dependents(e,{'first_person'})
    assert e.current_production.node('p5r_frames:'+first.shot_id).status==S.PENDING
    assert e.current_production.node('p5r_frames:'+other.shot_id).status==S.SUCCEEDED
    assert e.current_production.node('audio_voice:first_person').status==S.SUCCEEDED
    assert e.current_production.node('rough_cut').status==S.PENDING
    assert e.human_gates.get(old_review.review_id).status.value=='pending'
    assert e.human_gates.get(old_review.review_id).superseded_at is not None
    assert store.get(old.artifact_id,old.version).metadata['sha256']==old.metadata['sha256']
    assert refreshed==[e.current_project.project_id]
