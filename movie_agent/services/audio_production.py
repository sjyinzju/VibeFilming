"""Audio jobs on the existing production DAG and immutable Artifact store."""
from movie_agent.domain import (ArtifactType, GenerationJob, Provenance, ResourceClass,
    WorkflowEdge, WorkflowNode, WorkflowNodeStatus)
from movie_agent.media.contracts import (AudioCue, AudioPurpose, AudioTrack,
    CharacterVoiceProfile, DialogueCue, MediaReference, ReferenceType,
    SpeechGenerationRequest, VoiceDesignRequest, MusicGenerationRequest, SubtitleCue, SubtitleTrack)
from movie_agent.media.dialogue import extract_dialogue, check_duration
from movie_agent.media.post import fingerprint


def enabled(engine):
    return {'qwen3_tts', 'ace_step'} <= {p.provider_id for p in engine.media_runtime.providers.all()}


def settings(engine):
    return engine.media_runtime.providers.get('qwen3_tts').settings


def controls(engine):
    artifact = engine.artifact_store.get('audio_controls')
    return engine.artifact_store.read_structured(artifact) if artifact else {'voices': {}, 'dialogue': {}, 'music': {}}


def profiles(engine):
    return {a.metadata['character_id']: CharacterVoiceProfile.model_validate(engine.artifact_store.read_structured(a))
        for a in engine.artifact_store.list_all() if a.selected and a.metadata.get('purpose') == 'character_voice_profile'}


def cues(engine):
    artifact = engine.artifact_store.get('dialogue_cues')
    return [DialogueCue.model_validate(c) for c in engine.artifact_store.read_structured(artifact)] if artifact else []


def has_authoritative_dialogue(engine, shot):
    if not enabled(engine): return False
    if shot.narrative.dialogue or any(p.dialogue for p in shot.performances): return True
    if any(c.shot_id==shot.shot_id for c in cues(engine)): return True
    project=engine.current_project
    return bool(project.screenplay and sum(s.scene_id==shot.scene_id for s in project.shots)==1
        and any(s.scene_id==shot.scene_id and s.dialogue for s in project.screenplay.scenes))


def commit(engine, identity, content, purpose, *, provenance=None, **metadata):
    previous = engine.artifact_store.get(identity)
    if previous and engine.artifact_store.read_structured(previous) == content:
        return previous
    artifact = engine.artifact_store.create_structured(identity, content,
        metadata={'purpose': purpose, 'mock': False, **metadata},
        provenance=provenance or Provenance(project_id=engine.current_project.project_id, tool='audio_core'))
    engine.artifact_store.select(identity, artifact.version)
    return artifact


def install_nodes(engine, project):
    if not enabled(engine) or not project.shots:
        return
    graph = engine.current_production
    known = {n.node_id for n in graph.graph.nodes}
    if 'audio_prepare' in known:
        return
    # Audio requires committed shot planning, independently of video inference.
    graph.add_node(WorkflowNode(node_id='audio_prepare', node_type='audio_prepare',
        label='Prepare Dialogue', group='post', role='Sound/Post Director', display_order=115))
    graph.add_edge(WorkflowEdge(source_node_id='shot_gate', target_node_id='audio_prepare'))
    graph.add_edge(WorkflowEdge(source_node_id='audio_prepare', target_node_id='audio_post'))


def prepare(engine, project):
    config, active = controls(engine), profiles(engine)
    planned = extract_dialogue(project, active, settings(engine))
    for cue in planned:
        voice = config['voices'].get(cue.character_id, {})
        cue.voice_profile_version = voice.get('revision', active[cue.character_id].version if cue.character_id in active else 1)
        override = config['dialogue'].get(cue.cue_id, {})
        cue.emotion = override.get('emotion', cue.emotion)
        cue.pace = override.get('pace', cue.pace)
    commit(engine, 'dialogue_cues', [c.model_dump(mode='json') for c in planned], 'dialogue_cues')
    graph = engine.current_production
    def add(identity, label, kind, deps, output):
        if identity not in {n.node_id for n in graph.graph.nodes}:
            graph.add_node(WorkflowNode(node_id=identity, node_type=kind, label=label,
                group='post', parent_id='audio_post', role='Sound/Post Director', output_refs=[output], display_order=116))
            for dependency in deps:
                graph.add_edge(WorkflowEdge(source_node_id=dependency, target_node_id=identity))
            graph.add_edge(WorkflowEdge(source_node_id=identity, target_node_id='audio_post'))
    for char in sorted({c.character_id for c in planned}):
        add('audio_voice:'+char, 'Design Voice: '+char, 'voice_design', ['audio_prepare'], 'voice_profile_'+char)
    for cue in planned:
        add('audio_speech:'+cue.cue_id, 'Dialogue: '+cue.text[:32], 'speech',
            ['audio_prepare', 'audio_voice:'+cue.character_id], 'speech_'+cue.cue_id)
    for scene in sorted({s.scene_id for s in project.shots}):
        add('audio_music:'+scene, 'Music: '+scene, 'music', ['audio_prepare'], 'music_'+scene)
    engine._durable_media_checkpoint()


def prompt(engine, purpose, description):
    return engine.audio_prompt_compiler.compile(purpose, description, [])


async def generate(engine, node_id, request):
    signature = fingerprint(request.model_dump(mode='json', exclude={'created_at','request_id','prompt_package','job_id'}))
    job = GenerationJob(job_id='audio:'+signature, idempotency_key='audio:'+signature,
        project_id=request.project_id, scene_id=request.scene_id, shot_id=request.shot_id,
        node_id=node_id, task=request.purpose.value, resource_class=ResourceClass.MEDIUM,
        retry_budget=0, input_artifact_ids=[r.artifact_id for r in request.references],
        provenance=Provenance(project_id=request.project_id, role='Sound/Post Director', tool='audio_core'))
    artifact = await engine.media_runtime.generate_audio(job, request)
    engine.artifact_store.select(artifact.artifact_id, artifact.version)
    engine._durable_media_checkpoint()
    return artifact


async def execute(engine, project, node_id):
    if node_id == 'audio_prepare':
        prepare(engine, project)
        return
    config = controls(engine)
    if node_id.startswith('audio_voice:'):
        char = node_id.split(':',1)[1]
        character = next(c for c in project.characters if c.character_id == char)
        desired = config['voices'].get(char, {})
        active = profiles(engine).get(char)
        revision = desired.get('revision', 1)
        if active and active.version == revision:
            return
        instruction = desired.get('description') or '; '.join(part for part in (
            character.identity_description, '; '.join(character.voice_constraints),
            project.brief.voice_style) if part) or '清晰自然、声音稳定。'
        language = project.brief.output_language
        reference = next(c.text for c in cues(engine) if c.character_id == char)
        request = VoiceDesignRequest(job_id=node_id, project_id=project.project_id,
            character_id=char, text=reference, language=language, design_instruction=instruction,
            output_artifact_id='voice_anchor_'+char, seed=42+revision,
            prompt_package=prompt(engine, AudioPurpose.SPEECH, instruction), resource_class=ResourceClass.MEDIUM)
        anchor = await generate(engine, node_id, request)
        profile = CharacterVoiceProfile(voice_profile_id='voice_profile_'+char, version=revision,
            project_id=project.project_id, character_id=char, design_instruction=instruction,
            reference_artifact_id=anchor.artifact_id, reference_artifact_version=anchor.version,
            reference_sha256=anchor.metadata['sha256'], reference_text=reference, language=language,
            provider_id=anchor.provenance.provider_id, model_profile='qwen3-tts-voice-design → qwen3-tts-base')
        commit(engine, profile.voice_profile_id, profile.model_dump(mode='json'), 'character_voice_profile', character_id=char,
            provenance=Provenance(project_id=project.project_id, tool='audio_core',
                provider_id=anchor.provenance.provider_id, model_service_id=anchor.provenance.model_service_id,
                input_artifact_ids=[anchor.artifact_id], parent_artifact_id=anchor.artifact_id,
                parameters={'anchor_version':anchor.version,'anchor_sha256':profile.reference_sha256}))
    elif node_id.startswith('audio_speech:'):
        cue = next(c for c in cues(engine) if c.cue_id == node_id.split(':',1)[1])
        profile = profiles(engine)[cue.character_id]
        if profile.version != cue.voice_profile_version:
            raise ValueError('Dialogue references a different voice version')
        ref = MediaReference(reference_id='anchor_'+cue.character_id, reference_type=ReferenceType.VOICE,
            artifact_id=profile.reference_artifact_id, version=profile.reference_artifact_version,
            sha256=profile.reference_sha256)
        request = SpeechGenerationRequest(job_id=node_id, project_id=project.project_id,
            scene_id=cue.scene_id, shot_id=cue.shot_id, character_id=cue.character_id, text=cue.text,
            voice_profile=profile.voice_profile_id, voice_profile_version=profile.version,
            reference_voice=ref, references=[ref], reference_text=profile.reference_text,
            language=cue.language, emotion=cue.emotion, pace=cue.pace, prosody=cue.prosody,
            dialogue_cue_id=cue.cue_id, output_artifact_id='speech_'+cue.cue_id,
            seed=42+config['dialogue'].get(cue.cue_id, {}).get('revision', 0),
            prompt_package=prompt(engine, AudioPurpose.SPEECH, cue.text), resource_class=ResourceClass.MEDIUM)
        speech = await generate(engine, node_id, request)
        check_duration(cue, speech.metadata['duration_seconds'])
    elif node_id.startswith('audio_music:'):
        scene_id = node_id.split(':',1)[1]
        intent = config['music'].get(scene_id, {})
        if intent.get('enabled') is False: return
        scene = next((s for s in project.scenes if s.scene_id == scene_id), None)
        description = project.brief.music_style or ('Instrumental underscore supporting the film mood and pacing. Genre: '+
            ', '.join(project.brief.genre)+'. Themes: '+', '.join(project.brief.theme)+'. Pacing: '+project.brief.pacing.value)
        if scene:
            description += '; ' + scene.model_dump_json(exclude={'shots'})[:2000]
        if project.creative_direction:
            description += '; ' + project.creative_direction.model_dump_json()[:2000]
        if project.story_bible:
            description += '; ' + project.story_bible.model_dump_json()[:2000]
        duration = sum(s.duration_seconds for s in project.shots if s.scene_id == scene_id)
        request = MusicGenerationRequest(job_id=node_id, project_id=project.project_id,
            scene_id=scene_id, mood=description, duration_target_seconds=max(10,duration),
            output_artifact_id='music_'+scene_id, energy=intent.get('intensity',.35),
            seed=42+intent.get('revision',0), prompt_package=prompt(engine,AudioPurpose.MUSIC,description),
            resource_class=ResourceClass.MEDIUM)
        await generate(engine, node_id, request)


def assemble(engine, project):
    from movie_agent.services.post_production import selected_timeline
    timeline = selected_timeline(engine, project)
    config = controls(engine)
    native = [c for t in timeline.audio_tracks for c in t.cues if c.cue_type == AudioPurpose.GENERATED_NATIVE_AUDIO]
    speech, music, subtitles = [], [], []
    clips = {c.shot_id:c for t in timeline.video_tracks for c in t.clips}
    for cue in cues(engine):
        if cue.shot_id not in clips: continue
        artifact = engine.artifact_store.get('speech_'+cue.cue_id)
        if not artifact or artifact.metadata.get('mock') is not False:
            raise ValueError('Authoritative dialogue requires real TTS')
        start, end = check_duration(cue, artifact.metadata['duration_seconds'])
        start += clips[cue.shot_id].start_time_seconds
        end += clips[cue.shot_id].start_time_seconds
        speech.append(AudioCue(cue_id='mix_'+cue.cue_id, start_time_seconds=start,
            duration_seconds=end-start, cue_type=AudioPurpose.SPEECH,
            artifact_id=artifact.artifact_id, version=artifact.version, sha256=artifact.metadata['sha256'],
            scene_id=cue.scene_id, shot_id=cue.shot_id, dialogue_cue_id=cue.cue_id))
        subtitles.append(SubtitleCue(start_time_seconds=start,end_time_seconds=end,text=cue.text))
    for scene_id in sorted({s.scene_id for s in project.shots}):
        intent = config['music'].get(scene_id,{})
        if intent.get('enabled') is False: continue
        scene_shots = [s for s in project.shots if s.scene_id==scene_id]
        source_offsets, cursor = {}, 0
        for shot in scene_shots:
            source_offsets[shot.shot_id] = cursor
            cursor += shot.duration_seconds
        selected = sorted([clips[s.shot_id] for s in scene_shots if s.shot_id in clips],
                          key=lambda clip: clip.start_time_seconds)
        if not selected: continue
        artifact = engine.artifact_store.get('music_'+scene_id)
        if not artifact or artifact.metadata.get('mock') is not False:
            raise ValueError('Music requires real generation')
        runs = []
        for clip in selected:
            source_in = source_offsets[clip.shot_id] + clip.source_in_seconds
            if runs and abs(runs[-1]['start']+runs[-1]['duration']-clip.start_time_seconds)<.001 and abs(runs[-1]['source_in']+runs[-1]['duration']-source_in)<.001:
                runs[-1]['duration'] += clip.duration_seconds
            else:
                runs.append({'start':clip.start_time_seconds,'duration':clip.duration_seconds,'source_in':source_in})
        for i, run in enumerate(runs):
            music.append(AudioCue(cue_id=f'mix_music_{scene_id}_{i}', start_time_seconds=run['start'],
                duration_seconds=run['duration'],source_in_seconds=run['source_in'],cue_type=AudioPurpose.MUSIC,
                artifact_id=artifact.artifact_id, version=artifact.version, sha256=artifact.metadata['sha256'],
                gain_db=settings(engine).music_gain_db + (intent.get('intensity',.35)-.35)*12,
                fade_in_seconds=min(.5,run['duration']/2),fade_out_seconds=min(1,run['duration']/2),scene_id=scene_id))
    timeline.audio_tracks=[AudioTrack(track_id='production_sound',name='Production Sound',cues=native),
        AudioTrack(track_id='dialogue',name='Dialogue',cues=speech), AudioTrack(track_id='music',name='Music',cues=music)]
    timeline.subtitle_tracks=[SubtitleTrack(track_id='dialogue_subtitles',language=project.brief.output_language,cues=subtitles)] if subtitles and config.get('subtitles',True) else []
    engine.timeline=timeline
