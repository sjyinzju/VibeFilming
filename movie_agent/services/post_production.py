"""Real post handlers for the existing production DAG."""
from movie_agent.domain import (ArtifactType, Evaluation, EvaluationLayer, GenerationJob,
    Provenance, ResourceClass, ReviewStatus)
from movie_agent.media.contracts import (Timeline, TimelineClip, VideoTrack, AudioTrack,
    AudioCue, AudioPurpose, PostProductionRequest)
from movie_agent.media.post import pin_timeline


def real_post(engine):
    return any(p.provider_id == 'ffmpeg-post' for p in engine.media_runtime.providers.all())


def selected_timeline(engine, project):
    """Migrate legacy Mock audio explicitly; retain the canonical edit order/ranges."""
    artifacts = engine.artifact_store.list_all()
    old = engine.timeline
    if old and all(c.version and c.sha256 for t in old.video_tracks for c in t.clips):
        return pin_timeline(old,engine.artifact_store,engine.binary_store)
    clips = []
    cues = []
    if old:
        raw_clips = [c.model_copy(deep=True) for t in old.video_tracks for c in t.clips]
        if len(old.video_tracks) != 1: raise ValueError('Post requires one video track')
    else:
        raw_clips, offset = [], 0
        for shot in project.shots:
            matches = [a for a in artifacts if a.artifact_type==ArtifactType.VIDEO and a.selected
                       and a.provenance.shot_id==shot.shot_id]
            if len(matches)!=1: raise ValueError('Exactly one selected video is required per shot')
            raw_clips.append(TimelineClip(clip_id='post_clip_'+shot.shot_id,artifact_id=matches[0].artifact_id,shot_id=shot.shot_id,
                                         start_time_seconds=offset,duration_seconds=shot.duration_seconds))
            offset += shot.duration_seconds
    for clip in raw_clips:
        video = engine.artifact_store.get(clip.artifact_id,clip.version)
        if not video or (clip.version is None and not video.selected):
            raise ValueError('Post requires committed selected media')
        clip.version = video.version
        clips.append(clip)
        native = [a for a in artifacts if a.artifact_type==ArtifactType.AUDIO
                  and video.source_job_id and a.source_job_id==video.source_job_id
                  and a.metadata.get('media',{}).get('purpose')==AudioPurpose.GENERATED_NATIVE_AUDIO.value]
        if len(native)>1: raise ValueError('Ambiguous native audio lineage')
        if native:
            audio = native[0]
            cues.append(AudioCue(cue_id='post_audio_'+clip.clip_id,artifact_id=audio.artifact_id,version=audio.version,
                start_time_seconds=clip.start_time_seconds,duration_seconds=clip.duration_seconds,
                source_in_seconds=clip.source_in_seconds,cue_type=AudioPurpose.GENERATED_NATIVE_AUDIO,
                shot_id=clip.shot_id))
    timeline = Timeline(timeline_id='timeline_'+project.project_id,project_id=project.project_id,
        duration_seconds=old.duration_seconds if old else sum(c.duration_seconds for c in clips),
        video_tracks=[VideoTrack(track_id='post_video',clips=clips)],audio_tracks=[AudioTrack(track_id='post_audio',cues=cues)],
        subtitle_tracks=old.subtitle_tracks if old else [])
    # Stable identity when upgrading a legacy Timeline, without changing shot contracts.
    if old: timeline.timeline_id = old.timeline_id
    return pin_timeline(timeline,engine.artifact_store,engine.binary_store)


async def rough_cut(engine, project):
    timeline = selected_timeline(engine,project)
    engine.timeline = timeline
    content = timeline.model_dump(mode='json')
    committed = next((a for a in engine.artifact_store.list_versions('timeline')
                      if engine.artifact_store.read_structured(a)==content),None)
    if committed is None:
        committed = engine.artifact_store.create_structured('timeline',content,artifact_type=ArtifactType.TIMELINE,
            metadata={'purpose':'post_timeline','mock':False,'audio_policy':'real_native_or_silence; legacy Mock audio excluded'},
            provenance=Provenance(project_id=project.project_id,provider_id='ffmpeg-post',tool='timeline_commit'))
        engine._emit('artifact_created',project.project_id,{'artifact_id':committed.artifact_id,
            'artifact_type':'timeline','version':committed.version},node_id='rough_cut')
    engine.artifact_store.select('timeline',committed.version)
    width,height = engine._resolution(project.brief.resolution)
    request = PostProductionRequest(job_id='post:rough',project_id=project.project_id,timeline=timeline,
        output_artifact_id='rough_cut',width=width,height=height,fps=project.brief.fps,
        quality_profile=project.brief.quality_level,timeline_artifact_id=committed.artifact_id,
        timeline_version=committed.version,timeline_sha256=committed.metadata['sha256'])
    output = await engine.media_runtime.post_process(post_job(project,'rough_cut'),request)
    engine._select(project,output.artifact_id,output.version)


def post_job(project,node,parameters=None):
    return GenerationJob(job_id='post:'+node,project_id=project.project_id,node_id=node,task='post',
        resource_class=ResourceClass.MEDIUM,retry_budget=0,idempotency_key='post:'+node,
        provenance=Provenance(project_id=project.project_id,role='Sound/Post Director',
                              tool='media_runtime',parameters=parameters or {}))


async def full_film_review(engine,project):
    rough = engine._required_artifact('rough_cut')
    if rough.metadata.get('mock') is not False or not rough.metadata.get('qc',{}).get('passed'):
        raise ValueError('Final review requires a real QC-passed rough cut')
    engine.current_production.node('full_film_review').input_refs = ['rough_cut']
    engine._record_evaluation(project,Evaluation(layer=EvaluationLayer.TECHNICAL_QC,
        target_artifact_id=rough.artifact_id,target_artifact_version=rough.version,
        score=1,passed=True,
        summary='Deterministic FFmpeg film QC passed. Cinematic aesthetics require human review.'))


async def final_render(engine,project):
    review = next((r for r in reversed(engine.human_gates.all()) if r.node_id=='final_gate'
                   and r.status==ReviewStatus.APPROVED and r.superseded_at is None),None)
    if not review or not review.target_artifact_version or not review.target_sha256:
        raise ValueError('Real final render requires exact rough-cut approval')
    rough = engine.artifact_store.get(review.target_artifact_id,review.target_artifact_version)
    if (not rough or rough.metadata.get('sha256')!=review.target_sha256
            or rough.metadata.get('mock') is not False):
        raise ValueError('Approved rough-cut identity mismatch')
    plan = rough.metadata['render_plan']
    ref = plan['timeline']
    artifact = engine.artifact_store.get(ref['artifact_id'],ref['version'])
    timeline = Timeline.model_validate(engine.artifact_store.read_structured(artifact))
    request = PostProductionRequest(job_id='post:final',project_id=project.project_id,timeline=timeline,
        output_artifact_id='final_film',export_intent='approved_final',width=plan['delivery']['width'],height=plan['delivery']['height'],
        fps=plan['delivery']['fps'],quality_profile=plan['encoding']['profile'],
        timeline_artifact_id=ref['artifact_id'],timeline_version=ref['version'],timeline_sha256=ref['sha256'],
        render_plan=plan)
    output = await engine.media_runtime.post_process(post_job(project,'final_render',{
        'approved_review_id':review.review_id,'approved_rough_cut':{'artifact_id':rough.artifact_id,
            'version':rough.version,'sha256':rough.metadata['sha256']}}),request)
    engine._select(project,output.artifact_id,output.version)


async def technical_candidate(engine, project):
    """Export an evidence-checked candidate without resolving FINAL_CUT_APPROVAL."""
    rough=engine._required_artifact('rough_cut')
    if rough.metadata.get('mock') is not False or not rough.metadata.get('qc',{}).get('passed'):
        raise ValueError('Candidate requires a real technically passing rough cut')
    if any(issue.severity.value in {'major','critical'} for evaluation in engine.evaluations
           if evaluation.target_artifact_id==rough.artifact_id and evaluation.target_artifact_version==rough.version
           for issue in evaluation.issues):
        raise ValueError('Candidate has unresolved blocking film-level evidence')
    report=engine.artifact_store.get('film_quality_report')
    if not report: raise ValueError('Candidate requires film quality evidence')
    quality=engine.artifact_store.read_structured(report)
    accepted={s['shot_id']:s for s in quality['shots'] if s['status']=='accepted' and not s['blocking_issue_ids']}
    plan=rough.metadata['render_plan']; ref=plan['timeline']
    timeline_artifact=engine.artifact_store.get(ref['artifact_id'],ref['version'])
    if not timeline_artifact or timeline_artifact.metadata['sha256']!=ref['sha256']:
        raise ValueError('Candidate timeline hash mismatch')
    timeline=Timeline.model_validate(engine.artifact_store.read_structured(timeline_artifact))
    for track in timeline.video_tracks:
        for clip in track.clips:
            evidence=accepted.get(clip.shot_id)
            if not evidence or (clip.artifact_id,clip.version,clip.sha256)!=(
                evidence['artifact_id'],evidence['artifact_version'],evidence['sha256']):
                raise ValueError('Candidate contains footage without exact passing quality evidence')
    request=PostProductionRequest(job_id='post:technical-candidate',project_id=project.project_id,timeline=timeline,
        output_artifact_id='technical_candidate_final',export_intent='candidate',width=plan['delivery']['width'],height=plan['delivery']['height'],
        fps=plan['delivery']['fps'],quality_profile=plan['encoding']['profile'],timeline_artifact_id=ref['artifact_id'],
        timeline_version=ref['version'],timeline_sha256=ref['sha256'],render_plan=plan)
    output=await engine.media_runtime.post_process(post_job(project,'technical_candidate',{
        'authorization':'Technical candidate export under exact quality evidence; aesthetic review remains pending',
        'human_aesthetically_approved':False}),request)
    engine._select(project,output.artifact_id,output.version)
    return output
