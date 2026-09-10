"""Invalidate only visual descendants of changed canonical reference subjects."""
from movie_agent.domain import WorkflowNodeStatus as S,utc_now
from movie_agent.quality.references import hard_reference_subjects


def invalidate_reference_dependents(engine,subjects):
    project=engine.current_project;graph=engine.current_production;ns=engine.recovery_plan().namespace
    affected=set();shots=[]
    for shot in project.shots:
        scene=next(s for s in project.scenes if s.scene_id==shot.scene_id)
        consumed={subject for _,subject in hard_reference_subjects(shot,scene)}
        if project.visual_bible:consumed.add(project.visual_bible.visual_bible_id)
        if not (subjects & consumed):continue
        if not any(engine.artifact_store.get(prefix+shot.shot_id) for prefix in ('frame_gate_','video_','p5r_frame_pair_')):continue
        refs=engine.shot_references(project,shot)
        from movie_agent.media.video_conditioning import reference_fingerprint
        engine.commit_quality('reference_invalidation_'+shot.shot_id,{'shot_id':shot.shot_id,
            'reference_fingerprint':reference_fingerprint(refs),'changed_subjects':sorted(subjects),
            'reason':'Explicit reference selection changed; prior outputs remain historical, not current evidence.'},'reference_invalidation')
        shots.append(shot.shot_id)
        affected.update(ns+'_'+stage+':'+shot.shot_id for stage in ('frames','frame_gate','video','critic'))
    if not shots:return
    affected.update({'storyboard_planning','shot_production','technical_qc','visual_semantic_critic','cinematic_critic',
        'repair_accept',ns+'_candidate','audio_post','rough_cut','full_film_review','final_gate','final_render'})
    for node in graph.graph.nodes:
        if node.node_id in affected:
            node.status=S.PENDING;node.progress=0;node.completed_at=None
    for review in engine.human_gates.all():
        if review.node_id in affected and review.superseded_at is None:
            engine.human_gates._reviews[review.review_id]=review.model_copy(update={'superseded_at':utc_now()})
    project.canonical_state.completed_node_ids=[n.node_id for n in graph.graph.nodes if n.status==S.SUCCEEDED]
    engine.reports(project)
    engine._durable_media_checkpoint()
