"""Shared binary transport/QC for the existing AudioProvider boundary."""
import asyncio
from hashlib import sha256
import io
import json
import math
from pathlib import Path
import struct
import subprocess
import tempfile
import wave

import httpx
from movie_agent.domain import ProviderErrorType, ProviderKind, QualityProfile, ResourceClass
from movie_agent.media.contracts import (AudioGenerationResult, MediaEncoding, MediaModality,
    ProviderCapabilities, ResourceProfile)
from movie_agent.providers.base import ProviderFailure
from movie_agent.providers.media import AudioProvider, BinaryPayload, ProviderMediaResponse


def inspect_wav(content, ffprobe='ffprobe'):
    """Probe real bytes and independently inspect PCM samples. No ASR claims."""
    with tempfile.TemporaryDirectory(prefix='movie-audio-qc-') as folder:
        path = Path(folder) / 'audio.wav'
        path.write_bytes(content)
        result = subprocess.run([ffprobe, '-v', 'error', '-show_streams', '-show_format',
            '-of', 'json', str(path)], capture_output=True, timeout=30)
        if result.returncode:
            raise ValueError('Audio is not readable')
        data = json.loads(result.stdout)
        stream = next(s for s in data['streams'] if s['codec_type'] == 'audio')
        duration = float(stream.get('duration') or data['format']['duration'])
    with wave.open(io.BytesIO(content)) as wav:
        if wav.getsampwidth() != 2 or wav.getcomptype() != 'NONE':
            raise ValueError('Audio transport requires PCM16 WAV')
        rate, channels = wav.getframerate(), wav.getnchannels()
        count, maximum, squares = 0, 0, 0.0
        while chunk := wav.readframes(65536):
            for (sample,) in struct.iter_unpack('<h', chunk):
                count += 1
                maximum = max(maximum, abs(sample))
                squares += sample * sample
    if not count or not math.isfinite(duration) or duration <= 0:
        raise ValueError('Audio has no finite positive duration')
    peak = maximum / 32768
    rms = math.sqrt(squares / count) / 32768
    if rms < .0001:
        raise ValueError('Audio is silent or below -80 dBFS')
    if peak >= 32767 / 32768:
        raise ValueError('Audio contains clipped PCM samples')
    if abs(duration - count / channels / rate) > .01:
        raise ValueError('Audio duration disagrees with sample count')
    return {'duration_seconds': duration, 'sample_rate': rate, 'channels': channels,
        'rms_dbfs': 20*math.log10(rms), 'peak_dbfs': 20*math.log10(peak),
        'readable': True, 'not_silent': True, 'no_clipping': True,
        'human_listening': 'pending', 'sha256': sha256(content).hexdigest()}


class HTTPAudioProvider(AudioProvider):
    def __init__(self, *, settings, resolver=None, transport=None):
        self.settings, self.resolver, self.transport = settings, resolver, transport
        self.results = {}

    def client(self, timeout):
        return httpx.AsyncClient(timeout=timeout, trust_env=False, transport=self.transport,
                                 follow_redirects=False)

    async def status(self, request_id):
        return self.results.get(request_id)

    async def cancel(self, request_id):
        return False  # Neither API provides cancellation with terminal confirmation.

    def capability(self, tasks, audio):
        return ProviderCapabilities(provider_id=self.provider_id, kind=ProviderKind.AUDIO,
            modalities=[MediaModality.AUDIO], tasks=tasks, audio=audio,
            resource_profiles=[ResourceProfile(resource_class=r, supports_concurrency=False)
                               for r in ResourceClass], quality_profiles=list(QualityProfile),
            requires_resource_lease=True)

    async def binary(self, response):
        response.raise_for_status()
        if response.headers.get('content-type', '').split(';')[0] not in ('audio/wav', 'audio/x-wav'):
            raise ValueError('Audio endpoint did not return binary WAV')
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > 128 * 1024 * 1024:
                raise ValueError('Audio response exceeds size limit')
        return bytes(content)

    async def response(self, request, content, metadata):
        qc = await asyncio.to_thread(inspect_wav, content, self.settings.post_ffprobe)
        encoding = MediaEncoding(mime_type='audio/wav', format='wav', codec='pcm_s16le')
        result = AudioGenerationResult(request_id=request.request_id,
            artifact_ids=[request.output_artifact_id], primary_artifact_id=request.output_artifact_id,
            provider_id=self.provider_id, model_service_id=self.service_id,
            duration_seconds=qc['duration_seconds'], sample_rate=qc['sample_rate'],
            channels=qc['channels'], format='wav', purpose=request.purpose, encoding=encoding,
            provider_metadata={'mock': False, 'test_asset': False, 'audio_qc': qc, **metadata})
        self.results[request.request_id] = result
        return ProviderMediaResponse(result, (BinaryPayload(request.output_artifact_id, content,
            'audio/wav', 'wav', 'voice_anchor' if metadata.get('voice_design') else request.purpose.value),))


def transport_failure(error):
    if isinstance(error, httpx.TimeoutException):
        return ProviderFailure('Audio service timed out', ProviderErrorType.TIMEOUT, retryable=False)
    return ProviderFailure('Audio service request failed', ProviderErrorType.UNAVAILABLE, retryable=False)
