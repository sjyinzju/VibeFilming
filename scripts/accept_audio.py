"""Explicit opt-in P4D acceptance on a copy; every upstream inference is forbidden.

Requires built services and measured P4A budgets. Never fabricates a listening result
or approves the final gate. Serve this isolated project for the human review step.
"""
import argparse
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import shutil

from movie_agent.application.audio_commands import audio_command, AudioProductionCommand
from movie_agent.application.service import ProductionService
from movie_agent.config import LLMConfig
from movie_agent.execution.durable_events import DurableLocalEventBus
from movie_agent.media.dialogue import extract_dialogue
from movie_agent.model_services.wiring import build_spark_runtime
from movie_agent.providers.registry import MediaProviderSettings
from movie_agent.services.reasoning_production import ReasoningMovieProduction
from movie_agent.storage.projects import LocalProjectRepository
from tests.p2a_fakes import FakeReasoningProvider

SOURCE = Path('workspace/p4c-post-acceptance/project_3effeb45f3844bf89c2c96e83770114b')
ROOT = Path('workspace/p4d-audio-acceptance')


def compose(source=SOURCE, root=ROOT):
    source, root = source.resolve(), root.resolve()
    if not (source / 'record.json').is_file():
        raise ValueError('A completed existing real project is required')
    if root == source or root.is_relative_to(source) or source.is_relative_to(root):
        raise ValueError('Acceptance needs a separate destination')
    settings = MediaProviderSettings.from_env().model_copy(update={
        'audio_provider': 'real', 'post_provider': 'ffmpeg',
        'audio_task_state_root': str(root / 'tasks'), 'post_temp_root': str(root / 'temp')})
    if min(settings.tts_resident_gib, settings.tts_peak_gib,
           settings.music_resident_gib, settings.music_peak_gib) <= 0:
        raise ValueError('Measure isolated TTS/music telemetry and configure P4A budgets first')
    coordinator = build_spark_runtime(LLMConfig.from_env(), settings)
    if not coordinator.settings.enabled:
        coordinator.close()
        raise ValueError('Real acceptance requires enabled P4A resource management')
    target = root / source.name
    if not target.exists():
        shutil.copytree(source, target)
    attempted = []
    async def forbidden(*args, **kwargs):
        attempted.append('forbidden_upstream')
        raise AssertionError('P4D acceptance forbids Qwen/FLUX/H3/VLM inference')
    reasoning = FakeReasoningProvider()
    reasoning.submit = forbidden
    def factory(pid):
        engine = ReasoningMovieProduction(root / pid, reasoning, real_roles=[],
            event_bus=DurableLocalEventBus(root / pid / 'events'),
            media_settings=settings, runtime_coordinator=coordinator)
        for provider in engine.media_runtime.providers.all():
            if provider.provider_id not in {'qwen3_tts', 'ace_step'}:
                for name in ('generate', 'inspect'):
                    if hasattr(provider, name):
                        setattr(provider, name, forbidden)
        return engine
    service = ProductionService(LocalProjectRepository(root), factory)
    return service, coordinator, source.name, attempted


def file_hashes(root):
    return {str(p.relative_to(root)): sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


async def accept(service, coordinator, pid, attempted, source, root):
    before = file_hashes(source)
    engine = service.engine(pid)
    cues = extract_dialogue(engine.current_project, {}, MediaProviderSettings.from_env())
    if len(cues) < 2:
        raise ValueError('Acceptance requires at least two already committed dialogue cues')
    try:
        record = service.repository.get(pid)
        if record.status.value == 'waiting_human':
            print('Existing human review is pending; use --serve to inspect and resolve it.')
            return
        audio_command(service, pid, AudioProductionCommand(action='produce'))
        await service.tasks[pid]
        assert not attempted
        assert file_hashes(source) == before
        outputs = [a.model_dump(mode='json') for a in engine.artifact_store.list_all()
                   if a.selected and (a.artifact_type.value in {'audio', 'final_film'}
                                      or a.metadata.get('purpose') == 'character_voice_profile')]
        report = {'project_id': pid, 'status': service.repository.get(pid).status.value,
                  'original_workspace_unchanged': True, 'upstream_inference_attempts': attempted,
                  'human_listening': 'pending', 'artifacts': outputs}
        (root / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'project_id': pid, 'status': report['status'], 'human_listening': 'pending'}))
    finally:
        await service.shutdown()
        coordinator.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-real', action='store_true')
    parser.add_argument('--serve', action='store_true', help='Expose the same guarded service for human review')
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    if not args.run_real:
        parser.error('Pass --run-real explicitly; this can start TTS/music inference on Spark')
    service, coordinator, pid, attempted = compose(args.source, args.root)
    if args.serve:
        import uvicorn
        from movie_agent.api.app import create_app
        try:
            uvicorn.run(create_app(service, resource_runtime=coordinator), host='127.0.0.1', port=8087)
        finally:
            coordinator.close()
    else:
        asyncio.run(accept(service, coordinator, pid, attempted, args.source, args.root))


if __name__ == '__main__':
    main()
