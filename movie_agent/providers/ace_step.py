"""Official ACE-Step async API adapter with durable submission recovery."""
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
import httpx
from movie_agent.media.contracts import AudioCapabilities, MusicGenerationRequest
from movie_agent.providers.audio_transport import HTTPAudioProvider, transport_failure


class AceStepMusicProvider(HTTPAudioProvider):
    provider_id = 'ace_step'
    service_id = 'music'

    async def capabilities(self):
        return self.capability(['music'], AudioCapabilities(music=True))

    async def health(self):
        try:
            async with self.client(5) as client:
                result = await client.get(self.settings.music_endpoint + '/health')
                data=result.json().get('data', {})
                return result.is_success and data.get('status') == 'ok' and data.get('models_initialized') is True
        except (httpx.HTTPError, ValueError):
            return False

    async def generate(self, request):
        if not isinstance(request, MusicGenerationRequest) or request.provider_parameters:
            raise ValueError('Music requires a typed request without provider parameters')
        if not request.instrumental or request.extend_artifact_id or request.loop:
            raise ValueError('P4D supports new instrumental scene/act music only')
        payload = {'prompt': request.prompt_package.positive_prompt + ', instrumental, no vocals, no lyrics',
            'lyrics': '[Instrumental]', 'vocal_language': 'unknown',
            'audio_duration': request.duration_target_seconds or 15, 'batch_size': 1,
            'audio_format': 'wav', 'model': self.settings.music_model,
            'thinking': False, 'use_format': False, 'use_cot_caption': False,
            'use_cot_language': False, 'use_cot_metas': False,
            'seed': request.seed if request.seed is not None else 42}
        if request.tempo_bpm: payload['bpm'] = request.tempo_bpm
        key = sha256(json.dumps([request.project_id, payload], sort_keys=True).encode()).hexdigest()
        folder = Path(self.settings.audio_task_state_root)
        folder.mkdir(parents=True, exist_ok=True)
        journal = folder / (key + '.json')
        state = json.loads(journal.read_text()) if journal.exists() else None
        if state and state.get('status') == 'submitting':
            raise ValueError('Music submission outcome unknown; inspect service before retry')
        def save(value):
            temporary = journal.with_suffix('.tmp')
            temporary.write_text(json.dumps(value), encoding='utf-8')
            temporary.replace(journal)
        started = time.perf_counter()
        try:
            async with self.client(self.settings.music_timeout) as client:
                if not state:
                    save({'status': 'submitting'})
                    response = await client.post(self.settings.music_endpoint + '/release_task', json=payload)
                    response.raise_for_status()
                    state = {'status': 'submitted', 'task_id': response.json()['data']['task_id']}
                    save(state)
                task_id = state['task_id']
                async with asyncio.timeout(self.settings.music_timeout):
                    while True:
                        response = await client.post(self.settings.music_endpoint + '/query_result',
                                                     json={'task_id_list': [task_id]})
                        response.raise_for_status()
                        rows = response.json()['data']
                        row = next((r for r in rows if r.get('task_id') == task_id), None)
                        if not row: raise ValueError('Submitted music task was lost; explicit review required')
                        if row['status'] == 2: raise ValueError('Music generation failed')
                        if row['status'] == 1: break
                        await asyncio.sleep(self.settings.audio_poll_interval)
                result = json.loads(row['result']) if isinstance(row['result'], str) else row['result']
                url = result[0]['file']
                parsed = urlsplit(url)
                if parsed.scheme or parsed.netloc or parsed.path != '/v1/audio' or not parsed.query:
                    raise ValueError('Music result must use the same service audio endpoint')
                async with client.stream('GET', self.settings.music_endpoint + url) as response:
                    content = await self.binary(response)
                output = await self.response(request, content, {'model': self.settings.music_model,
                    'remote_task_id': task_id, 'music_prompt': payload['prompt'], 'mood': request.mood,
                    'instrumental_requested': True, 'vocals_human_check': 'pending',
                    'latency_seconds': time.perf_counter()-started})
                if output.result.channels != 2: raise ValueError('Music must be stereo')
                save({'status': 'succeeded', 'task_id': task_id})
                return output
        except httpx.HTTPError as error:
            raise transport_failure(error) from error
