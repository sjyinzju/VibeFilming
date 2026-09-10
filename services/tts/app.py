"""Offline VoiceDesign → immutable anchor → Base clone binary audio service."""
from contextlib import asynccontextmanager
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import threading
import time

from fastapi import FastAPI, Form, HTTPException, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class SpeechInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: str = 'qwen3-tts-base'
    input: str = Field(min_length=1, max_length=4000)
    language: str = 'Chinese'
    reference_text: str = ''
    reference_sha256: str = Field(default='', pattern=r'^(?:[a-f0-9]{64})?$')
    instruction: str = ''
    emotion: str = ''
    pace: str = 'medium'
    seed: int = Field(default=42, ge=0)


class DesignInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: str = 'qwen3-tts-voice-design'
    input: str = Field(min_length=1, max_length=1000)
    language: str = 'Chinese'
    instruction: str = Field(min_length=1, max_length=2000)
    seed: int = Field(default=42, ge=0)


models = {}
loads = {}
slot = threading.Lock()
cache = Path(os.environ.get('TTS_CACHE_ROOT', '/output'))


@asynccontextmanager
async def lifespan(app):
    import torch
    from qwen_tts import Qwen3TTSModel
    root = Path(os.environ.get('TTS_MODEL_ROOT', '/models/qwen3-tts'))
    cache.mkdir(parents=True, exist_ok=True)
    for name in ('voice-design', 'base'):
        started = time.perf_counter()
        # Existing model directories include their matching speech_tokenizer.
        models[name] = Qwen3TTSModel.from_pretrained(str(root / name),
            device_map='cuda:0', dtype=torch.bfloat16, attn_implementation='sdpa',
            local_files_only=True)
        torch.cuda.synchronize()
        loads[name] = time.perf_counter() - started
    yield


app = FastAPI(lifespan=lifespan)


@app.get('/health')
def health():
    return {'ready': len(models) == 2, 'state': 'busy' if slot.locked() else 'ready',
            'model_load_seconds': loads, 'attention': 'sdpa', 'offline': True,
            'native_emotion_control': False, 'native_pace_control': False}


@app.get('/v1/models')
def inventory():
    return {'data': [{'id': 'qwen3-tts-' + name} for name in models]}


def produce(kind, request, anchor=None):
    import numpy as np
    import soundfile as sf
    import torch
    data = request.model_dump()
    identity = sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
                      + (anchor or b'') + kind.encode()).hexdigest()
    target = cache / (identity + '.wav')
    started = time.perf_counter()
    with slot:
        reused = target.exists()
        if not reused:
            torch.manual_seed(request.seed)
            if kind == 'voice-design':
                waves, rate = models[kind].generate_voice_design(text=request.input,
                    language=request.language, instruct=request.instruction, max_new_tokens=2048)
            else:
                wave, rate = sf.read(io.BytesIO(anchor), dtype='float32')
                if wave.ndim > 1:
                    wave = np.mean(wave, axis=1)
                waves, rate = models[kind].generate_voice_clone(text=request.input,
                    language=request.language, ref_audio=(wave, rate),
                    ref_text=request.reference_text, max_new_tokens=2048)
            torch.cuda.synchronize()
            temporary = target.with_suffix('.tmp')
            sf.write(temporary, waves[0], rate, format='WAV', subtype='PCM_16')
            temporary.replace(target)
        content = target.read_bytes()
        if kind == 'voice-design':
            # Keep generated anchors on Spark; cloning can reference this digest
            # without uploading any real media asset from the client.
            anchors = cache / 'anchors'
            anchors.mkdir(exist_ok=True)
            digest = sha256(content).hexdigest()
            anchor_path = anchors / (digest + '.wav')
            if not anchor_path.exists():
                os.link(target, anchor_path)
            (anchors / (digest + '.json')).write_text(json.dumps({
                'text':request.input,'language':request.language},ensure_ascii=False),encoding='utf-8')
    return Response(content, media_type='audio/wav', headers={
        'X-Audio-SHA256': sha256(content).hexdigest(),
        'X-Generation-Seconds': str(time.perf_counter() - started),
        'X-Cache-Reused': str(reused).lower(), 'X-Model': request.model,
        'X-Mock': 'false'})


@app.post('/v1/voices/design')
def design(request: DesignInput):
    if request.model != 'qwen3-tts-voice-design':
        raise HTTPException(422, 'Unsupported voice-design model')
    return produce('voice-design', request)


@app.post('/v1/audio/speech')
def speech(request: str = Form(...), reference: UploadFile | None = File(default=None)):
    try:
        value = SpeechInput.model_validate_json(request)
    except ValidationError as error:
        raise HTTPException(422, 'Invalid speech metadata') from error
    if value.model != 'qwen3-tts-base':
        raise HTTPException(422, 'Unsupported voice-clone model')
    if value.instruction or value.emotion or value.pace not in ('medium', 'natural'):
        raise HTTPException(422, 'Base clone has no native instruction, emotion or pace control')
    if reference is None:
        anchor_path = cache / 'anchors' / (value.reference_sha256 + '.wav')
        if not anchor_path.is_file():
            raise HTTPException(404, 'Generated voice anchor is not present on this service')
        metadata = json.loads(anchor_path.with_suffix('.json').read_text(encoding='utf-8'))
        if metadata['text'] != value.reference_text:
            raise HTTPException(422, 'Reference transcript differs from generated anchor')
        with anchor_path.open('rb') as stream:
            anchor = stream.read(20 * 1024 * 1024 + 1)
    else:
        anchor = reference.file.read(20 * 1024 * 1024 + 1)
    if len(anchor) > 20 * 1024 * 1024 or sha256(anchor).hexdigest() != value.reference_sha256:
        raise HTTPException(422, 'Reference size or SHA256 mismatch')
    if not value.reference_text:
        raise HTTPException(422, 'Exact anchor transcript is required')
    return produce('base', value, anchor)
