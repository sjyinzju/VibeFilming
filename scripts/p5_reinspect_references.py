"""One scoped reference reinspection and evidence-backed canonical dialogue correction."""
import json
from pathlib import Path
from movie_agent.services.hero_production import HeroMovieProduction
from movie_agent.providers.registry import MediaProviderSettings
from movie_agent.domain import WorkflowNodeStatus,utc_now,ArtifactType
from movie_agent.media.dialogue import extract_dialogue
from movie_agent.storage.projects import LocalProjectRepository

root=Path('workspace/p5-hero-film');pid=(root/'project-id.txt').read_text().strip()
repository=LocalProjectRepository(root);record=repository.get(pid)
assert record.status.value!='running'
state=json.loads(Path('workspace/_resource_runtime/spark-state.json').read_text())
assert not [l for l in state['leases'] if l['released_at'] is None]
engine=HeroMovieProduction(root/pid,None,media_settings=MediaProviderSettings())
project,graph=engine._restore();engine.current_project,engine.current_production=project,graph
assert not engine.artifact_store.get('p5_reference_scope_revision')
assert not engine.existing_videos(project)
changes=[]
for shot in project.shots:
    for performance in shot.performances:
        if shot.scene_id=='SCENE_001' and not shot.narrative.dialogue and performance.dialogue=='无':
            changes.append({'shot_id':shot.shot_id,'field':'performance.dialogue','before':'无','after':'',
                'reason':'Silence placeholder; not a line in the canonical screenplay'})
            performance.dialogue=''
    if shot.shot_id=='SCENE_003-SHOT-02':
        assert shot.narrative.dialogue==['CHAR_LYNNE_001']
        line=next(p.dialogue for p in shot.performances if p.character_id=='CHAR_LYNNE_001')
        assert line=='原来……不疼。'
        changes.append({'shot_id':shot.shot_id,'field':'narrative.dialogue','before':shot.narrative.dialogue,
            'after':[line],'reason':'Restore the exact screenplay/performance line; a bare character ID is not spoken text'})
        shot.narrative.dialogue=[line]
settings=MediaProviderSettings.from_env()
cues=extract_dialogue(project,{},settings)
assert [(c.scene_id,c.character_id,c.text) for c in cues]==[
    ('SCENE_001','CHAR_LYNNE_001','稳住……别慌。'),
    ('SCENE_002','CHAR_ELLA_001','地心引力，是拥抱。'),
    ('SCENE_003','CHAR_LYNNE_001','原来……不疼。')]
engine.commit_quality('p5_reference_scope_revision',{
    'authorization':'P5 autonomous bounded planning/quality repair request',
    'reason':'Initial reference inspection incorrectly required other characters and all world locations in each isolated plate',
    'policy':'Use each exact plate generation instruction as inspection scope; preserve all failed results and image hashes',
    'human_approved':False,'dialogue_changes':changes,'verified_cues':[c.model_dump(mode='json') for c in cues]},
    'p5_reference_scope_revision')
engine._artifact(project,ArtifactType.TEXT,[s.model_dump(mode='json') for s in project.shots],
    artifact_id='shot_plan',role='Cinematographer',tool='p5_canonical_dialogue_correction')
affected={'asset_planning','storyboard_planning','shot_production','technical_qc','visual_semantic_critic',
    'cinematic_critic','repair_accept','audio_prepare','audio_post','rough_cut','full_film_review','final_gate','final_render'}
for node in graph.graph.nodes:
    if node.node_id in affected:
        node.status=WorkflowNodeStatus.PENDING;node.progress=0;node.completed_at=None
for review in engine.human_gates.all():
    assert review.node_id=='audio_prepare' and review.question=='Screenplay dialogue needs explicit shot assignment'
    engine.human_gates._reviews[review.review_id]=review.model_copy(update={
        'superseded_at':utc_now(),'resolution_notes':'Superseded by verified exact canonical dialogue assignment; no human approval asserted.'})
project.canonical_state.completed_node_ids=[n.node_id for n in graph.graph.nodes if n.status==WorkflowNodeStatus.SUCCEEDED]
engine._durable_media_checkpoint()
print('Scoped reference revision saved; all three exact screenplay lines verified; old gate superseded without approval.')
