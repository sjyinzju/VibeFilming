"""Deterministic execution projection of the existing immutable Timeline."""
from __future__ import annotations

from fractions import Fraction
from hashlib import sha256
import json
import math
from typing import Literal

from pydantic import Field
from movie_agent.domain import ContractModel, Provenance, ArtifactType
from movie_agent.media.contracts import Timeline, AudioPurpose


def fingerprint(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                             allow_nan=False).encode()).hexdigest()


def stream_hash(stream) -> str:
    digest = sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


class ExactPostSource(ContractModel):
    artifact_id: str
    version: int = Field(ge=1)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    provider: str
    source_job_id: str | None = None
    mock: Literal[False] = False


class PostSegment(ContractModel):
    shot_id: str | None
    video: ExactPostSource
    audio: ExactPostSource | None = None
    video_facts: dict
    audio_facts: dict | None = None
    planned_duration: float
    effective_duration: float
    start_frame: int
    end_frame: int
    start: float
    end: float
    video_source_in: float
    video_source_out: float
    audio_source_in: float = 0
    audio_source_out: float = 0
    audio_offset_samples: int = 0
    audio_samples: int
    audio_content_samples: int = 0
    audio_gain_db: float = 0
    audio_fade_in: float = 0
    audio_fade_out: float = 0
    generated_silence: bool = False
    transition: Literal['cut'] = 'cut'
    conform_actions: list[str]


class PostRenderPlan(ContractModel):
    project_id: str
    render_plan_id: str
    timeline: dict
    delivery: dict
    encoding: dict
    audio_policy: dict
    segments: list[PostSegment]
    subtitles: list[dict] = Field(default_factory=list)
    audio_layers: list[dict] = Field(default_factory=list)
    duration: float
    frame_count: int
    policy_version: str = 'p4c.2'
    mock: Literal[False] = False


def source_identity(artifact, binaries, project_id) -> ExactPostSource:
    if artifact is None or artifact.provenance.project_id != project_id:
        raise ValueError('Post source does not belong to this project')
    meta = artifact.metadata
    provider = artifact.provenance.provider_id or artifact.provenance.tool or ''
    if (meta.get('mock') is not False or meta.get('media', {}).get('mock') is not False
            or 'mock' in provider.lower() or meta.get('test_asset') or meta.get('media', {}).get('test_asset')):
        raise ValueError('Real post rejects Mock or unverified media')
    with binaries.open(artifact.uri) as stream:
        digest = stream_hash(stream)
    if meta.get('sha256') and meta['sha256'] != digest:
        raise ValueError('Registered source hash mismatch')
    return ExactPostSource(artifact_id=artifact.artifact_id, version=artifact.version,
        sha256=digest, provider=provider, source_job_id=artifact.source_job_id)


def pin_timeline(timeline: Timeline, artifacts, binaries) -> Timeline:
    """Selection is resolved once, before immutable Timeline commit, never at render."""
    pinned = timeline.model_copy(deep=True)
    for item in [c for t in pinned.video_tracks for c in t.clips] + [c for t in pinned.audio_tracks for c in t.cues]:
        artifact = artifacts.get(item.artifact_id, item.version)
        identity = source_identity(artifact, binaries, timeline.project_id)
        if item.sha256 and item.sha256 != identity.sha256:
            raise ValueError('Timeline source hash mismatch')
        item.version, item.sha256 = identity.version, identity.sha256
    return pinned


def build_plan(request, resolver, probe, settings) -> PostRenderPlan:
    timeline = request.timeline
    if timeline.project_id != request.project_id or len(timeline.video_tracks) != 1:
        raise ValueError('Post requires one project-owned sequential video track')
    if not request.timeline_artifact_id or not request.timeline_version or not request.timeline_sha256:
        raise ValueError('Real post requires an exact immutable Timeline')
    committed = resolver.artifacts.get(request.timeline_artifact_id, request.timeline_version)
    if (not committed or committed.provenance.project_id != request.project_id
            or committed.metadata.get('sha256') != request.timeline_sha256
            or Timeline.model_validate(resolver.artifacts.read_structured(committed)).model_dump(mode='json') != timeline.model_dump(mode='json')):
        raise ValueError('Immutable Timeline does not match request')
    if request.width % 2 or request.height % 2 or request.width > 7680 or request.height > 7680:
        raise ValueError('H.264 yuv420p delivery requires even dimensions <= 7680')
    if request.output_format != 'mp4' or request.video_codec != 'h264' or request.audio_codec != 'aac':
        raise ValueError('Unsupported post encoding contract')
    if request.quality_profile.value not in ('draft', 'standard', 'high', 'showcase') or not 1 <= request.fps <= 120:
        raise ValueError('Unsupported post quality or delivery fps')
    fps = Fraction(str(request.fps)).limit_denominator(1001000)
    def frame(time): return math.floor(Fraction(str(time)) * fps + Fraction(1, 2))
    def exact(item, modality):
        if item.version is None or not item.sha256:
            raise ValueError('Post inputs must pin artifact/version/SHA256')
        artifact = resolver.artifacts.get(item.artifact_id, item.version)
        identity = source_identity(artifact, resolver.binaries, request.project_id)
        if identity.sha256 != item.sha256 or artifact.artifact_type.value != modality:
            raise ValueError('Pinned post source mismatch')
        return identity, probe(resolver.binaries.describe(artifact.uri).path, modality)
    cues = [c for t in timeline.audio_tracks for c in t.cues]
    if len({c.cue_id for c in cues}) != len(cues):
        raise ValueError('Audio cue IDs must be unique')
    # Validate all explicit audio, including unused cues: no silent Mock filtering.
    audio = {c.cue_id: exact(c, 'audio') for c in cues}
    mixed = any(c.dialogue_cue_id or c.cue_type == AudioPurpose.MUSIC for c in cues)
    layers = []
    if mixed:
        for cue in cues:
            identity, facts = audio[cue.cue_id]
            if cue.cue_type not in (AudioPurpose.GENERATED_NATIVE_AUDIO, AudioPurpose.SPEECH, AudioPurpose.MUSIC):
                raise ValueError('P4D mix supports production sound, dialogue and music')
            if cue.start_time_seconds+cue.duration_seconds > timeline.duration_seconds+.001:
                raise ValueError('Audio cue exceeds Timeline')
            if cue.source_in_seconds >= facts['duration']:
                raise ValueError('Audio source range is empty')
            if cue.cue_type == AudioPurpose.SPEECH and facts['duration']-cue.source_in_seconds > cue.duration_seconds+.02:
                raise ValueError('Speech must fit its cue without truncation')
            layers.append({'cue_id': cue.cue_id, 'source': identity.model_dump(mode='json'),
                'facts': facts, 'purpose': cue.cue_type.value, 'start': float(cue.start_time_seconds),
                'duration': float(cue.duration_seconds), 'source_in': float(cue.source_in_seconds),
                'gain_db': float(cue.gain_db), 'fade_in': float(cue.fade_in_seconds), 'fade_out': float(cue.fade_out_seconds),
                'dialogue_cue_id': cue.dialogue_cue_id, 'enabled': cue.enabled})
        speech = sorted((l for l in layers if l['purpose']=='speech' and l['enabled']),key=lambda l:l['start'])
        if any(a['start']+a['duration'] > b['start']+.001 for a,b in zip(speech,speech[1:])):
            raise ValueError('Overlapping authoritative dialogue requires explicit timing repair')
    used, segments, cursor = set(), [], 0
    for clip in timeline.video_tracks[0].clips:
        start, end = frame(clip.start_time_seconds), frame(clip.start_time_seconds + clip.duration_seconds)
        if start != cursor or end <= start or clip.transition not in (None, '', 'cut', 'CUT'):
            raise ValueError('P4C requires contiguous positive CUT segments; edit Timeline explicitly')
        video, vf = exact(clip, 'video')
        duration = float(Fraction(end-start, 1) / fps)
        source_out = min(vf['duration'] - clip.trim_end_seconds, clip.source_in_seconds + duration)
        if source_out <= clip.source_in_seconds:
            raise ValueError('Video source range is empty')
        matches = [c for c in cues if (not mixed or c.cue_type == AudioPurpose.GENERATED_NATIVE_AUDIO)
                   and c.enabled and c.start_time_seconds < float(Fraction(end, 1)/fps)-1e-7
                   and c.start_time_seconds+c.duration_seconds > float(Fraction(start, 1)/fps)+1e-7]
        if len(matches) > 1:
            raise ValueError('P4C supports one audio cue per segment; overlapping mix intent unsupported')
        actions = ['reset_video_pts', 'reset_audio_pts', 'frame_boundary_conform',
                   'preserve_aspect_scale_letterbox', 'CFR', 'audio_resample_48000_stereo', 'cut']
        if clip.source_in_seconds > 0 or source_out < vf['duration']:
            actions.append('video_trim')
        if source_out-clip.source_in_seconds < duration-1e-6:
            actions.append('video_freeze_pad')
        segment = PostSegment(shot_id=clip.shot_id, video=video, video_facts=vf,
            planned_duration=clip.duration_seconds, effective_duration=duration,
            start_frame=start, end_frame=end, start=float(Fraction(start,1)/fps), end=float(Fraction(end,1)/fps),
            video_source_in=clip.source_in_seconds, video_source_out=source_out,
            audio_samples=round(float(Fraction(end,1)/fps)*48000)-round(float(Fraction(start,1)/fps)*48000),
            generated_silence=not matches, conform_actions=actions)
        if matches:
            cue = matches[0]
            identity, af = audio[cue.cue_id]
            if cue.cue_id in used or cue.start_time_seconds < segment.start-1/float(fps):
                raise ValueError('Audio cue must belong to one segment')
            if cue.cue_type == AudioPurpose.GENERATED_NATIVE_AUDIO and (
                    not video.source_job_id or identity.source_job_id != video.source_job_id):
                raise ValueError('Native audio must share exact video source job')
            if cue.start_time_seconds + cue.duration_seconds > segment.end+1/float(fps):
                raise ValueError('Audio cue crosses segment boundary')
            used.add(cue.cue_id)
            segment.audio, segment.audio_facts = identity, af
            segment.audio_source_in = cue.source_in_seconds
            segment.audio_source_out = min(af['duration'], cue.source_in_seconds + cue.duration_seconds)
            if segment.audio_source_out <= segment.audio_source_in or not math.isfinite(cue.gain_db):
                raise ValueError('Invalid audio source range or gain')
            segment.audio_offset_samples = max(0, round((cue.start_time_seconds-segment.start)*48000))
            segment.audio_content_samples = min(round(cue.duration_seconds*48000), segment.audio_samples-segment.audio_offset_samples)
            segment.audio_gain_db = cue.gain_db
            segment.audio_fade_in, segment.audio_fade_out = cue.fade_in_seconds, cue.fade_out_seconds
            segment.conform_actions.extend(['audio_trim', 'audio_silence_pad'])
        else:
            segment.conform_actions.append('generated_silence')
        segments.append(segment)
        cursor = end
    expected_audio = {c.cue_id for c in cues if c.enabled and (not mixed or c.cue_type == AudioPurpose.GENERATED_NATIVE_AUDIO)}
    if not segments or used != expected_audio or cursor != frame(timeline.duration_seconds):
        raise ValueError('Timeline duration or audio placement is inconsistent')
    subtitles = []
    for track in timeline.subtitle_tracks:
        for cue in track.cues:
            if not 0 <= cue.start_time_seconds < cue.end_time_seconds <= float(Fraction(cursor,1)/fps):
                raise ValueError('Invalid subtitle cue timing')
            subtitles.append({**cue.model_dump(mode='json'), 'language': track.language, 'alignment': 'timeline_cue'})
    if len(timeline.subtitle_tracks) > 1:
        raise ValueError('P4C supports one subtitle sidecar language')
    high = request.quality_profile.value in ('high', 'showcase')
    plan = PostRenderPlan(project_id=request.project_id, render_plan_id='',
        timeline={'artifact_id': committed.artifact_id, 'version': committed.version,
                  'sha256': request.timeline_sha256, 'timeline_id': timeline.timeline_id},
        delivery={'width': request.width, 'height': request.height, 'fps': float(fps),
                  'fps_rational': str(fps), 'aspect_ratio': f'{request.width}:{request.height}'},
        encoding={'profile': request.quality_profile.value, 'video_codec': 'h264', 'encoder': 'libx264',
                  'pix_fmt': 'yuv420p', 'crf': 18 if high else 21, 'preset': 'slow' if high else 'medium',
                  'audio_codec': 'aac', 'audio_bitrate': '192k', 'faststart': True},
        audio_policy={'sample_rate': 48000, 'channels': 2, 'layout': 'stereo', 'stretch': False,
                      'loudness_lufs': settings.post_loudness_lufs, 'true_peak_db': settings.post_true_peak_db,
                      'normalization': 'whole_film_two_pass_loudnorm', 'drift_tolerance_seconds': 1/float(fps)},
        segments=segments, subtitles=subtitles, duration=float(Fraction(cursor,1)/fps), frame_count=cursor,
        audio_layers=layers)
    if mixed:
        plan.policy_version = 'p4d.1'
        plan.audio_policy.update(native_audio_duck_db=getattr(settings,'native_audio_duck_db',-16),
            music_duck_db=getattr(settings,'music_duck_db',-6),
            audio_duck_attack_seconds=getattr(settings,'audio_duck_attack_seconds',.12),
            audio_duck_release_seconds=getattr(settings,'audio_duck_release_seconds',.35),
            subtitle_mode=getattr(settings,'post_subtitle_mode','sidecar'))
    excluded = {'render_plan_id'} if mixed else {'render_plan_id','audio_layers'}
    plan.render_plan_id = 'post_plan_' + fingerprint(plan.model_dump(mode='json', exclude=excluded))
    return plan


def srt_bytes(plan):
    def stamp(t):
        ms = round(t*1000)
        return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'
    rows = []
    for i, cue in enumerate(sorted(plan.subtitles, key=lambda c:c['start_time_seconds']), 1):
        text = cue['text'].replace('\r', '').replace('\x00', '').strip()
        rows.append(f"{i}\n{stamp(cue['start_time_seconds'])} --> {stamp(cue['end_time_seconds'])}\n{text}\n")
    return ('\n'.join(rows)).encode('utf-8')
