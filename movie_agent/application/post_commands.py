"""Explicit post-only revision; no upstream planning or generation invalidation."""
from typing import Literal
from pydantic import Field
from movie_agent.domain import ContractModel, WorkflowNodeStatus, utc_now, new_id
from movie_agent.media.contracts import SubtitleTrack, SubtitleCue
from movie_agent.services.post_production import real_post, selected_timeline
from .repository import ProductionStatus as Status
from .service import CommandConflict


class PostExportCommand(ContractModel):
    resolution: str | None = Field(default=None,pattern=r'^[1-9][0-9]{1,3}x[1-9][0-9]{1,3}$')
    fps: float | None = Field(default=None,ge=1,le=120,allow_inf_nan=False)
    quality: Literal['standard','high'] | None = None
    subtitles: bool | None = None
    shot_order: list[str] | None = None
    audio_gain_db: float | None = Field(default=None,ge=-60,le=12,allow_inf_nan=False)
    use_selected_sources: bool = False


def reexport(service,project_id,command):
    engine = service.engine(project_id)
    record = service.repository.get(project_id)
    if not real_post(engine): raise CommandConflict('Re-export requires FFmpeg post')
    if record.status in {Status.RUNNING,Status.PAUSING,Status.CANCELLED}:
        raise CommandConflict('Post revision requires an idle non-cancelled project')
    affected={'audio_post','rough_cut','full_film_review','final_gate','final_render'}
    from movie_agent.services.audio_production import enabled as real_audio
    if real_audio(engine):
        # A post-only edit uses the exact edited audio Timeline; no audio reassembly.
        affected.discard('audio_post')
    if any(n.status!=WorkflowNodeStatus.SUCCEEDED for n in engine.current_production.graph.nodes if n.node_id not in affected):
        raise CommandConflict('Complete upstream production before re-export')
    if any(r.node_id not in affected and r.status.value!='approved' and r.superseded_at is None for r in engine.human_gates.all()):
        raise CommandConflict('Resolve upstream reviews before re-export')
    timeline = selected_timeline(engine,engine.current_project)
    if command.use_selected_sources:
        # Explicitly repin only on this command, never during an in-flight render.
        from movie_agent.media.post import pin_timeline
        for track in timeline.video_tracks:
            for clip in track.clips:
                clip.version=None;clip.sha256=None
        previous=engine.timeline
        try:
            engine.timeline=timeline
            timeline=selected_timeline(engine,engine.current_project)
        finally:
            engine.timeline=previous
    if command.shot_order is not None:
        clips={c.shot_id:c for t in timeline.video_tracks for c in t.clips}
        order=command.shot_order
        if not order or len(order)!=len(set(order)) or not set(order)<=set(clips):
            raise CommandConflict('Shot order must contain unique existing shot IDs')
        track_cues=[(t.track_id,c) for t in timeline.audio_tracks for c in t.cues]
        old_subtitles=[c for t in timeline.subtitle_tracks for c in t.cues]
        shifted_subtitles=[];shifted_audio={t.track_id:[] for t in timeline.audio_tracks};offset=0
        for shot_id in order:
            clip=clips[shot_id]
            delta=offset-clip.start_time_seconds
            for track_id,cue in track_cues:
                if cue.shot_id==shot_id:
                    shifted_audio[track_id].append(cue.model_copy(update={'start_time_seconds':cue.start_time_seconds+delta}))
                elif cue.shot_id is None and cue.cue_type.value=='music':
                    start=max(cue.start_time_seconds,clip.start_time_seconds)
                    end=min(cue.start_time_seconds+cue.duration_seconds,clip.start_time_seconds+clip.duration_seconds)
                    if end>start:
                        shifted_audio[track_id].append(cue.model_copy(update={
                            'cue_id':cue.cue_id+'_'+clip.clip_id,'shot_id':shot_id,
                            'start_time_seconds':start+delta,'duration_seconds':end-start,
                            'source_in_seconds':cue.source_in_seconds+start-cue.start_time_seconds,
                            'fade_in_seconds':cue.fade_in_seconds if start==cue.start_time_seconds else 0,
                            'fade_out_seconds':cue.fade_out_seconds if end==cue.start_time_seconds+cue.duration_seconds else 0}))
            for cue in old_subtitles:
                if clip.start_time_seconds<=cue.start_time_seconds and cue.end_time_seconds<=clip.start_time_seconds+clip.duration_seconds:
                    shifted_subtitles.append(cue.model_copy(update={'start_time_seconds':cue.start_time_seconds+delta,
                                                                  'end_time_seconds':cue.end_time_seconds+delta}))
            clip.start_time_seconds=offset;offset+=clip.duration_seconds
        timeline.video_tracks[0].clips=[clips[s] for s in order]
        for track in timeline.audio_tracks: track.cues=shifted_audio[track.track_id]
        if timeline.subtitle_tracks: timeline.subtitle_tracks[0].cues=shifted_subtitles
        timeline.duration_seconds=offset
    if command.audio_gain_db is not None:
        for track in timeline.audio_tracks:
            for cue in track.cues: cue.gain_db=command.audio_gain_db
    if command.subtitles is False: timeline.subtitle_tracks=[]
    elif command.subtitles and not timeline.subtitle_tracks:
        if real_audio(engine):
            from movie_agent.services.audio_production import cues as dialogue_cues
            canonical={c.cue_id:c for c in dialogue_cues(engine)}
            cues=[SubtitleCue(start_time_seconds=c.start_time_seconds,end_time_seconds=c.start_time_seconds+c.duration_seconds,
                             text=canonical[c.dialogue_cue_id].text)
                  for t in timeline.audio_tracks for c in t.cues if c.enabled and c.dialogue_cue_id in canonical]
        else:
            shots={s.shot_id:s for s in engine.current_project.shots}
            cues=[SubtitleCue(start_time_seconds=c.start_time_seconds,end_time_seconds=c.start_time_seconds+c.duration_seconds,
                             text='\n'.join(shots[c.shot_id].narrative.dialogue))
                  for t in timeline.video_tracks for c in t.clips if c.shot_id in shots and shots[c.shot_id].narrative.dialogue]
        timeline.subtitle_tracks=[SubtitleTrack(track_id='post_subtitles',language=engine.current_project.brief.output_language,cues=cues)]
    brief=engine.current_project.brief.model_copy(deep=True)
    if command.resolution:
        width,height=map(int,command.resolution.split('x'))
        if width%2 or height%2 or max(width,height)>7680: raise CommandConflict('Delivery requires even dimensions <= 7680')
        brief.resolution=command.resolution
    if command.fps: brief.fps=command.fps
    if command.quality: brief.quality_level=command.quality
    engine.current_project.brief=brief
    # An explicit export command creates an edit revision even when settings match.
    # Restarts retain this checkpointed identity and reuse its exact render job.
    timeline.timeline_id=new_id('timeline')
    engine.timeline=timeline
    for node in engine.current_production.graph.nodes:
        if node.node_id in affected:
            node.status=WorkflowNodeStatus.PENDING;node.progress=0;node.completed_at=None
    for review in engine.human_gates.all():
        if review.node_id in affected:
            engine.human_gates._reviews[review.review_id]=review.model_copy(update={'superseded_at':utc_now()})
    engine.current_project.canonical_state.completed_node_ids=[n.node_id for n in engine.current_production.graph.nodes if n.status==WorkflowNodeStatus.SUCCEEDED]
    engine._durable_media_checkpoint()
    record.status=Status.PAUSED;record.failure_code=None;service.repository.save(record)
    return service.start(project_id,resume=True)
