"""Real Qwen3-TTS VoiceDesign/Base adapter; no provider endpoint in Domain."""
from hashlib import sha256
import json
import time
import httpx
from movie_agent.media.contracts import AudioCapabilities, SpeechGenerationRequest, VoiceDesignRequest
from movie_agent.providers.audio_transport import HTTPAudioProvider, transport_failure


def language_name(value):
    return {'zh': 'Chinese', 'zh-cn': 'Chinese', '中文': 'Chinese', 'en': 'English',
            'en-us': 'English', '英文': 'English'}.get(value.lower(), value)


class Qwen3TTSAudioProvider(HTTPAudioProvider):
    provider_id = 'qwen3_tts'
    service_id = 'tts'

    async def capabilities(self):
        return self.capability(['speech'], AudioCapabilities(tts=True, voice_clone=True))

    async def health(self):
        try:
            async with self.client(5) as client:
                result = await client.get(self.settings.tts_endpoint + '/health')
                return result.is_success and result.json().get('ready') is True
        except (httpx.HTTPError, ValueError):
            return False

    async def generate(self, request):
        if not isinstance(request, (SpeechGenerationRequest, VoiceDesignRequest)):
            raise ValueError('TTS requires a typed speech or voice design request')
        if request.provider_parameters:
            raise ValueError('TTS does not accept arbitrary provider parameters')
        data = {'input': request.text, 'language': language_name(request.language),
                'seed': request.seed if request.seed is not None else 42}
        metadata = {'text': request.text, 'character_id': request.character_id,
                    'language': request.language}
        started = time.perf_counter()
        try:
            async with self.client(self.settings.tts_timeout) as client:
                if isinstance(request, VoiceDesignRequest):
                    data.update(model='qwen3-tts-voice-design', instruction=request.design_instruction)
                    metadata.update(voice_design=True, design_instruction=request.design_instruction)
                    outbound = client.build_request('POST', self.settings.tts_endpoint + '/v1/voices/design', json=data)
                else:
                    ref = request.reference_voice
                    if not ref or not ref.version or not ref.sha256 or not request.reference_text or not self.resolver:
                        raise ValueError('Voice clone requires an exact anchor and transcript')
                    artifact = self.resolver.artifacts.get(ref.artifact_id, ref.version)
                    if not artifact or artifact.provenance.project_id != request.project_id or artifact.metadata.get('mock') is not False:
                        raise ValueError('Anchor must be real and project-owned')
                    with self.resolver.binaries.open(artifact.uri) as stream:
                        anchor = stream.read(20*1024*1024+1)
                    if len(anchor)>20*1024*1024 or sha256(anchor).hexdigest()!=ref.sha256:
                        raise ValueError('Voice anchor hash or size mismatch')
                    data.update(model=self.settings.tts_model, reference_text=request.reference_text,
                                reference_sha256=ref.sha256)
                    # Base exposes identity cloning, not native emotional instruction.
                    # Preserve intent and disclose unsupported controls in provenance/UI.
                    metadata.update(voice_profile_id=request.voice_profile,
                        voice_profile_version=request.voice_profile_version,
                        dialogue_cue_id=request.dialogue_cue_id, reference=ref.model_dump(mode='json'),
                        requested_emotion=request.emotion, requested_pace=request.pace,
                        requested_prosody=request.prosody, native_prosody_control=False,
                        reference_transport='service_sha256')
                    outbound = client.build_request('POST', self.settings.tts_endpoint + '/v1/audio/speech',
                        data={'request': json.dumps(data, ensure_ascii=False)})
                response = await client.send(outbound, stream=True)
                try:
                    content = await self.binary(response)
                finally:
                    await response.aclose()
                metadata.update(model=data['model'], latency_seconds=time.perf_counter()-started,
                    service_generation_seconds=response.headers.get('x-generation-seconds'),
                    service_cache_reused=response.headers.get('x-cache-reused')=='true')
                return await self.response(request, content, metadata)
        except httpx.HTTPError as error:
            raise transport_failure(error) from error
