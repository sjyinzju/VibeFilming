"""Typed user controls; only dependent audio and post nodes are invalidated."""
from typing import Literal
from pydantic import Field
from movie_agent.domain import ContractModel, WorkflowNodeStatus, utc_now
from movie_agent.services import audio_production as audio
from .repository import ProductionStatus as Status
from .service import CommandConflict


class AudioProductionCommand(ContractModel):
    action: Literal['produce', 'redesign_voice', 'regenerate_speech', 'music', 'listening_result']
    character_id: str | None = None
    cue_id: str | None = None
    scene_id: str | None = None
    voice_description: str | None = Field(default=None,min_length=1,max_length=2000)
    emotion: str | None = Field(default=None,max_length=200)
    pace: Literal['slow','medium','fast'] | None = None
    enabled: bool | None = None
    intensity: float | None = Field(default=None,ge=0,le=1)
    listening_passed: bool | None = None
    notes: str | None = Field(default=None,max_length=4000)


def audio_command(service, project_id, command):
    engine = service.engine(project_id)
    record = service.repository.get(project_id)
    if record.status in {Status.RUNNING,Status.PAUSING,Status.CANCELLED}:
        raise CommandConflict('Audio commands require an idle project')
    if not audio.enabled(engine):
        raise CommandConflict('Configure real TTS and music providers first')
    project = engine.current_project
    if not project.shots:
        raise CommandConflict('Commit shot planning before audio production')
    post_nodes={'audio_post','rough_cut','full_film_review','final_gate','final_render'}
    if any(n.status!=WorkflowNodeStatus.SUCCEEDED for n in engine.current_production.graph.nodes
           if n.node_id not in post_nodes and not n.node_id.startswith('audio_')):
        raise CommandConflict('Complete upstream production before an audio-only revision')
    config = audio.controls(engine)
    if command.action == 'listening_result':
        if command.listening_passed is None or not command.notes or not command.notes.strip():
            raise CommandConflict('Listening acceptance requires an explicit result and notes')
        final = engine.artifact_store.get('final_film')
        if not final or final.metadata.get('mock') is not False or not final.metadata.get('audio_stems'):
            raise CommandConflict('Listening acceptance requires a completed three-track final film')
        audio.commit(engine,'audio_listening_result',{'passed':command.listening_passed,
            'notes':command.notes,'recorded_at':utc_now().isoformat(),
            'final_artifact':{'artifact_id':final.artifact_id,'version':final.version,'sha256':final.metadata.get('sha256')} if final else None,
            'speech':[{'artifact_id':a.artifact_id,'version':a.version,'sha256':a.metadata.get('sha256')} for a in engine.artifact_store.list_all()
                      if a.selected and a.metadata.get('dialogue_cue_id')]}, 'audio_listening_result')
        engine._durable_media_checkpoint()
        from .views import CommandAccepted
        return CommandAccepted(project_id=project_id,status=record.status)
    affected={'audio_post','rough_cut','full_film_review','final_gate','final_render'}
    if command.action == 'redesign_voice':
        if command.character_id not in {c.character_id for c in project.characters} or not command.voice_description:
            raise CommandConflict('Choose a project character and voice description')
        active=audio.profiles(engine).get(command.character_id)
        previous=config['voices'].get(command.character_id,{})
        config['voices'][command.character_id]={'description':command.voice_description,
            'revision':max(previous.get('revision',0),active.version if active else 0)+1}
        affected.update({'audio_prepare','audio_voice:'+command.character_id})
        affected.update('audio_speech:'+c.cue_id for c in audio.cues(engine) if c.character_id==command.character_id)
    elif command.action == 'regenerate_speech':
        if command.cue_id not in {c.cue_id for c in audio.cues(engine)}:
            raise CommandConflict('Choose an existing canonical dialogue cue')
        previous=config['dialogue'].get(command.cue_id,{})
        config['dialogue'][command.cue_id]={**previous,'revision':previous.get('revision',0)+1}
        for key in ('emotion','pace'):
            if getattr(command,key) is not None: config['dialogue'][command.cue_id][key]=getattr(command,key)
        affected.update({'audio_prepare','audio_speech:'+command.cue_id})
    elif command.action == 'music':
        if command.scene_id not in {s.scene_id for s in project.shots}:
            raise CommandConflict('Choose an existing scene')
        config['music'].setdefault(command.scene_id,{})
        for key in ('enabled','intensity'):
            if getattr(command,key) is not None: config['music'][command.scene_id][key]=getattr(command,key)
        # Existing music is reused; this command changes its Timeline mix only.
        if command.enabled and not engine.artifact_store.get('music_'+command.scene_id):
            affected.add('audio_music:'+command.scene_id)
    elif command.action == 'produce':
        affected.update(n.node_id for n in engine.current_production.graph.nodes if n.node_id.startswith('audio_'))
    audio.commit(engine,'audio_controls',config,'audio_controls')
    audio.install_nodes(engine,project)
    for node in engine.current_production.graph.nodes:
        if node.node_id in affected:
            node.status=WorkflowNodeStatus.PENDING;node.progress=0;node.completed_at=None
    for review in engine.human_gates.all():
        if review.node_id in affected:
            engine.human_gates._reviews[review.review_id]=review.model_copy(update={'superseded_at':utc_now()})
    project.canonical_state.completed_node_ids=[n.node_id for n in engine.current_production.graph.nodes if n.status==WorkflowNodeStatus.SUCCEEDED]
    engine._durable_media_checkpoint()
    record.status=Status.PAUSED;record.failure_code=None;service.repository.save(record)
    return service.start(project_id,resume=True)
