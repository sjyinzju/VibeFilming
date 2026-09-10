"""Local CPU post provider. No shell, model runtime, or Mock fallback."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from fractions import Fraction
import inspect
import json
import math
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time

from movie_agent.domain import JobStatus, ProviderKind, QualityProfile, ResourceClass, utc_now
from movie_agent.media.contracts import (MediaDimensions, MediaEncoding, MediaModality,
    PostProductionResult, ProviderCapabilities, ResourceProfile)
from movie_agent.media.post import PostRenderPlan, build_plan, source_identity, stream_hash
from movie_agent.providers.media import (BinaryPayload, MediaProviderProgress, PostProcessor,
                                         ProviderMediaResponse)

_LOCAL_RENDER_SLOT = threading.Semaphore(1)


class FFmpegPostProcessor(PostProcessor):
    provider_id = 'ffmpeg-post'

    def __init__(self, *, settings, resolver):
        self.settings, self.resolver = settings, resolver
        self._cancelled = {}
        self._results = {}

    async def capabilities(self):
        return ProviderCapabilities(provider_id=self.provider_id, kind=ProviderKind.VIDEO,
            modalities=[MediaModality.POST], tasks=['post'],
            resource_profiles=[ResourceProfile(resource_class=ResourceClass.MEDIUM,
                                                supports_concurrency=False)],
            quality_profiles=[QualityProfile.STANDARD, QualityProfile.HIGH, QualityProfile.SHOWCASE],
            requires_resource_lease=False)

    async def health(self):
        return bool(shutil.which(self.settings.post_ffmpeg) and shutil.which(self.settings.post_ffprobe))

    async def status(self, request_id):
        return self._results.get(request_id)

    async def cancel(self, request_id):
        event = self._cancelled.get(request_id)
        if event: event.set()
        return event is not None

    def _capture(self, argv):
        try:
            result = subprocess.run(argv, capture_output=True, timeout=self.settings.post_timeout, check=False)
        except subprocess.TimeoutExpired as error:
            raise TimeoutError('Post decoder timeout') from error
        if result.returncode:
            raise ValueError('Post media decoder failed')
        return result.stdout.decode('utf-8', errors='replace')

    def probe(self, path, modality):
        data = json.loads(self._capture([self.settings.post_ffprobe, '-v', 'error', '-count_frames',
            '-show_streams', '-show_format', '-of', 'json', str(path)]))
        stream = next((s for s in data.get('streams', []) if s.get('codec_type') == modality), None)
        if not stream:
            raise ValueError(f'Post requires a readable {modality} stream')
        duration = float(stream.get('duration') or data['format'].get('duration', 0))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('Media has no finite positive duration')
        facts = {'duration': duration, 'codec': stream['codec_name'],
                 'start_time': float(stream.get('start_time', 0)), 'time_base': stream.get('time_base')}
        if modality == 'video':
            facts.update(width=stream['width'], height=stream['height'], pix_fmt=stream.get('pix_fmt'),
                fps=float(Fraction(stream['avg_frame_rate'])), fps_rational=stream['avg_frame_rate'],
                frame_count=int(stream.get('nb_read_frames') or stream.get('nb_frames') or 0),
                sample_aspect_ratio=stream.get('sample_aspect_ratio', '1:1'))
        else:
            facts.update(sample_rate=int(stream['sample_rate']), channels=stream['channels'])
        return facts

    async def prepare_request(self, request):
        if request.render_plan:
            plan = PostRenderPlan.model_validate(request.render_plan)
            # An approved projection is immutable, but the request cannot replace its intent.
            if (plan.project_id != request.project_id or plan.timeline['artifact_id'] != request.timeline_artifact_id
                    or plan.timeline['version'] != request.timeline_version
                    or plan.timeline['sha256'] != request.timeline_sha256
                    or (plan.delivery['width'], plan.delivery['height'], plan.delivery['fps'])
                    != (request.width, request.height, request.fps)):
                raise ValueError('Approved post plan differs from request')
            from movie_agent.media.post import fingerprint
            excluded = {'render_plan_id'} if plan.audio_layers else {'render_plan_id','audio_layers'}
            if plan.render_plan_id != 'post_plan_' + fingerprint(plan.model_dump(mode='json', exclude=excluded)):
                raise ValueError('Render plan integrity failure')
            from types import SimpleNamespace
            pinned_settings = SimpleNamespace(post_loudness_lufs=plan.audio_policy['loudness_lufs'],
                post_true_peak_db=plan.audio_policy['true_peak_db'],
                **{key:plan.audio_policy[key] for key in ('native_audio_duck_db','music_duck_db',
                    'audio_duck_attack_seconds','audio_duck_release_seconds') if key in plan.audio_policy},
                post_subtitle_mode=plan.audio_policy.get('subtitle_mode','sidecar'))
            verified = await asyncio.to_thread(build_plan,request,self.resolver,self.probe,pinned_settings)
            if verified != plan:
                raise ValueError('Render plan differs from immutable Timeline or decoder facts')
            return request
        plan = await asyncio.to_thread(build_plan, request, self.resolver, self.probe, self.settings)
        return request.model_copy(update={'render_plan': plan.model_dump(mode='json')})

    def _run(self, argv, work, event, emit, phase, duration=None):
        """Drain progress concurrently; bounded diagnostics never escape the worker."""
        with tempfile.TemporaryFile(dir=work) as errors:
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=errors,
                                       stdin=subprocess.DEVNULL, shell=False)
            def progress():
                values = {}
                for raw in process.stdout:
                    key, _, value = raw.decode(errors='replace').strip().partition('=')
                    values[key] = value
                    if key == 'progress':
                        seconds = max(0, int(values.get('out_time_us', '0'))/1e6)
                        emit(phase, min(seconds/duration, .999) if duration else None,
                             values.get('frame'), values.get('speed'))
            reader = threading.Thread(target=progress, daemon=True)
            reader.start()
            deadline = time.monotonic()+self.settings.post_timeout
            try:
                while process.poll() is None:
                    if event.wait(.1):
                        raise RuntimeError('Post render cancelled')
                    if time.monotonic() > deadline:
                        raise TimeoutError('FFmpeg post timeout')
                if process.returncode:
                    raise ValueError(f'FFmpeg {phase} failed (exit {process.returncode})')
            finally:
                if process.poll() is None: process.kill()
                process.wait()
                reader.join(timeout=5)
                process.stdout.close()
            errors.seek(0, 2)
            errors.seek(max(0, errors.tell()-65536))
            return errors.read().decode('utf-8', errors='replace')

    def _base(self):
        return [self.settings.post_ffmpeg, '-hide_banner', '-nostdin', '-y', '-nostats',
                '-progress', 'pipe:1', '-filter_complex_threads', '1', '-threads', '2']

    def _path(self, source, project_id):
        artifact = self.resolver.artifacts.get(source.artifact_id, source.version)
        verified = source_identity(artifact, self.resolver.binaries, project_id)
        if verified != source:
            raise ValueError('Render source changed since plan commit')
        return self.resolver.binaries.describe(artifact.uri).path

    def _render(self, request, event, emit):
        plan = PostRenderPlan.model_validate(request.render_plan)
        started, clock = utc_now(), time.monotonic()
        root = Path(self.settings.post_temp_root) if self.settings.post_temp_root else None
        if root: root.mkdir(parents=True, exist_ok=True)
        while not _LOCAL_RENDER_SLOT.acquire(timeout=.1):
            if event.is_set(): raise RuntimeError('Post render cancelled')
        try:
            # Prefix contains only a hash, never a user filename/title or FFmpeg argument.
            with tempfile.TemporaryDirectory(prefix=plan.render_plan_id[:28]+'-', dir=root) as temporary:
                work = Path(temporary)
                versions = {name: self._capture([executable, '-version']).splitlines()[0]
                    for name, executable in [('ffmpeg',self.settings.post_ffmpeg), ('ffprobe',self.settings.post_ffprobe)]}
                fps, width, height = plan.delivery['fps_rational'], plan.delivery['width'], plan.delivery['height']
                for index, segment in enumerate(plan.segments):
                    argv = self._base()+['-i', str(self._path(segment.video, plan.project_id))]
                    if segment.audio:
                        argv += ['-i', str(self._path(segment.audio, plan.project_id))]
                        audio = (f'[1:a:0]asetpts=PTS-STARTPTS,atrim=start={segment.audio_source_in}:end={segment.audio_source_out},'
                            'asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,'
                            f'volume={segment.audio_gain_db}dB,apad,atrim=end_sample={segment.audio_content_samples}')
                        if segment.audio_fade_in:
                            audio += f',afade=t=in:d={segment.audio_fade_in}'
                        if segment.audio_fade_out:
                            audio += f',afade=t=out:st={max(0,segment.audio_content_samples/48000-segment.audio_fade_out)}:d={segment.audio_fade_out}'
                        audio += f',adelay={segment.audio_offset_samples}S:all=1,apad,atrim=end_sample={segment.audio_samples},asetpts=N/SR/TB[a]'
                    else:
                        audio = f'anullsrc=r=48000:cl=stereo,atrim=end_sample={segment.audio_samples},asetpts=N/SR/TB[a]'
                    # Scale using display aspect (including non-square source pixels), then letterbox.
                    video = (f'[0:v:0]setpts=PTS-STARTPTS,trim=start={segment.video_source_in}:end={segment.video_source_out},setpts=PTS-STARTPTS,'
                        f'scale=w=trunc(ih*dar/2)*2:h=ih,setsar=1,'
                        f'scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,'
                        f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}:round=near,'
                        f'tpad=stop_mode=clone:stop_duration={segment.effective_duration},'
                        f'trim=end_frame={segment.end_frame-segment.start_frame},setpts=N/({fps}*TB),format=yuv420p[v]')
                    argv += ['-filter_complex', video+';'+audio, '-map', '[v]', '-map', '[a]',
                        '-c:v', 'libx264', '-preset', plan.encoding['preset'], '-crf', str(plan.encoding['crf']),
                        '-threads', '2', '-c:a', 'pcm_s24le', '-fps_mode', 'cfr',
                        '-video_track_timescale', str(Fraction(fps).numerator*1000), str(work/f'clip{index}.mov')]
                    self._run(argv,work,event,emit,f'conform {index+1}/{len(plan.segments)}',segment.effective_duration)
                (work/'concat.txt').write_text(''.join(f"file 'clip{i}.mov'\n" for i in range(len(plan.segments))), encoding='ascii')
                # Video durations are exact frame counts; PCM avoids per-clip AAC encoder delay.
                joined = work/'joined.mov'
                self._run(self._base()+['-f','concat','-safe','1','-i',str(work/'concat.txt'),
                    '-map','0:v:0','-map','0:a:0','-c','copy','-video_track_timescale',str(Fraction(fps).numerator*1000),
                    str(joined)],work,event,emit,'assemble',plan.duration)
                stems = []
                if plan.audio_layers:
                    from movie_agent.media.audio_mix import render_stems
                    stems,mixed = render_stems(self,plan,work,event,emit)
                    mixed_joined = work/'mixed_joined.mov'
                    self._run(self._base()+['-i',str(joined),'-i',str(mixed),'-map','0:v:0','-map','1:a:0',
                        '-c','copy','-video_track_timescale',str(Fraction(fps).numerator*1000),str(mixed_joined)],
                        work,event,emit,'attach final mix',plan.duration)
                    joined=mixed_joined
                target = plan.audio_policy
                norm = f"loudnorm=I={target['loudness_lufs']}:TP={target['true_peak_db']}:LRA=11"
                log = self._run(self._base()+['-i',str(joined),'-vn','-af',norm+':print_format=json',
                    '-f','null','-'],work,event,emit,'loudness analysis',plan.duration)
                matches = re.findall(r'\{\s*"input_i"[\s\S]*?\}',log)
                if not matches: raise ValueError('Missing loudnorm measurements')
                measurements = json.loads(matches[-1])
                silence = measurements['input_i'] == '-inf'
                if silence:
                    loudness_filter = 'anull'
                else:
                    for key in ('input_i','input_tp','input_lra','input_thresh','target_offset'):
                        if not math.isfinite(float(measurements[key])): raise ValueError('Invalid loudness analysis')
                    loudness_filter = norm + (f":measured_I={measurements['input_i']}:measured_TP={measurements['input_tp']}"
                        f":measured_LRA={measurements['input_lra']}:measured_thresh={measurements['input_thresh']}"
                        f":offset={measurements['target_offset']}:linear=true:print_format=json")
                final = work/'output.mp4'
                total_samples = sum(s.audio_samples for s in plan.segments)
                video_args = ['-c:v','copy']
                if plan.subtitles and target.get('subtitle_mode') == 'burn-in':
                    from movie_agent.media.post import srt_bytes
                    (work/'subtitles.srt').write_bytes(srt_bytes(plan))
                    subtitle_path = str(work/'subtitles.srt').replace('\\','/').replace(':', r'\:')
                    video_args=['-vf',f"subtitles=filename='{subtitle_path}'",'-c:v','libx264',
                        '-preset',plan.encoding['preset'],'-crf',str(plan.encoding['crf']),'-pix_fmt','yuv420p']
                log2 = self._run(self._base()+['-i',str(joined),'-map','0:v:0','-map','0:a:0',
                    *video_args,'-af',loudness_filter+f',aresample=48000,apad,atrim=end_sample={total_samples},asetpts=N/SR/TB',
                    '-c:a','aac','-b:a',plan.encoding['audio_bitrate'],'-ar','48000','-ac','2',
                    '-video_track_timescale', str(Fraction(fps).numerator*1000),
                    '-movflags','+faststart','-map_metadata','-1',str(final)],work,event,emit,'final loudness / encode',plan.duration)
                emit('technical QC',None,None,None)
                qc = self.qc(final,plan)
                decoded_log = self._run(self._base()+['-i',str(final),'-vn','-af',norm+':print_format=json',
                    '-f','null','-'],work,event,emit,'decoded audio QC',plan.duration)
                decoded_matches = re.findall(r'\{\s*"input_i"[\s\S]*?\}',decoded_log)
                if not decoded_matches: raise ValueError('Missing decoded audio QC')
                decoded = json.loads(decoded_matches[-1])
                if not silence and (float(decoded['input_tp']) > min(0,target['true_peak_db']+.5)
                        or abs(float(decoded['input_i'])-target['loudness_lufs'])>1.5):
                    raise ValueError('Final decoded audio loudness/peak QC failed')
                qc['decoded_audio_loudness'] = decoded
                normalized = re.findall(r'\{\s*"input_i"[\s\S]*?\}',log2)
                loudness = {'input': measurements, 'output': json.loads(normalized[-1]) if normalized else None,
                            'all_silence': silence, 'target': plan.audio_policy}
                with final.open('rb') as stream: digest = stream_hash(stream)
                # Disk-backed transferable stream; runtime owns and closes it after immutable commit.
                payload = tempfile.TemporaryFile(dir=root)
                with final.open('rb') as stream: shutil.copyfileobj(stream,payload,1024*1024)
                payload.seek(0)
                metadata = {'mock': False, 'test_asset': False, 'sha256':digest,
                    'fps':plan.delivery['fps'], 'video_codec':'h264','pix_fmt':'yuv420p',
                    'audio_codec':'aac','sample_rate':48000,'channels':2,'qc':qc,
                    'render_plan_id':plan.render_plan_id,'render_plan':plan.model_dump(mode='json'),
                    'loudness':loudness,'versions':versions,'started_at':started.isoformat(),
                    'render_seconds':round(time.monotonic()-clock,4), 'completed_at':utc_now().isoformat()}
                result = PostProductionResult(request_id=request.request_id,provider_id=self.provider_id,
                    artifact_ids=[request.output_artifact_id],primary_artifact_id=request.output_artifact_id,
                    duration_seconds=qc['video']['duration'],dimensions=MediaDimensions(width=width,height=height,
                    aspect_ratio=plan.delivery['aspect_ratio']),encoding=MediaEncoding(mime_type='video/mp4',format='mp4',codec='h264'),
                    provider_metadata=metadata)
                payloads=[BinaryPayload(request.output_artifact_id,payload,'video/mp4','mp4',request.output_artifact_id)]
                for label,path in stems:
                    transfer=tempfile.TemporaryFile(dir=root)
                    with path.open('rb') as stream: shutil.copyfileobj(stream,transfer,1024*1024)
                    transfer.seek(0)
                    payloads.append(BinaryPayload('stem_'+label+'_'+plan.render_plan_id[10:],transfer,'audio/wav','wav',label+'_stem'))
                return ProviderMediaResponse(result,tuple(payloads))
        finally:
            _LOCAL_RENDER_SLOT.release()

    def qc(self, path, plan):
        video, audio = self.probe(path,'video'), self.probe(path,'audio')
        tolerance = plan.audio_policy['drift_tolerance_seconds']
        drift = abs(video['start_time']+video['duration']-audio['start_time']-audio['duration'])
        timestamps = json.loads(self._capture([self.settings.post_ffprobe,'-v','error','-select_streams','v:0',
            '-show_entries','frame=best_effort_timestamp_time','-of','json',str(path)]))['frames']
        pts = [float(f['best_effort_timestamp_time']) for f in timestamps]
        cfr = all(abs((b-a)-1/plan.delivery['fps']) <= 0.00001 for a,b in zip(pts,pts[1:]))
        atoms = []
        with path.open('rb') as stream:
            while header := stream.read(8):
                if len(header)!=8: break
                size,kind = struct.unpack('>I4s',header)
                header_size = 8
                if size==1:
                    size=struct.unpack('>Q',stream.read(8))[0];header_size=16
                if size<header_size: break
                atoms.append(kind.decode('ascii',errors='replace'))
                stream.seek(size-header_size,1)
        checks = {'readable':True,'video_stream':True,'audio_stream':True,'positive_size':path.stat().st_size>0,
            'resolution':(video['width'],video['height'])==(plan.delivery['width'],plan.delivery['height']),
            # Container duration rounding can alter avg_frame_rate by micro-fps.
            # The independent per-frame PTS and exact frame-count checks remain strict.
            'fps':abs(video['fps']-plan.delivery['fps'])<=1e-4,'CFR':cfr,
            'frame_count':video['frame_count']==plan.frame_count,
            'video_codec':video['codec']=='h264','pix_fmt':video['pix_fmt']=='yuv420p',
            'audio_codec':audio['codec']=='aac','sample_rate':audio['sample_rate']==48000,'channels':audio['channels']==2,
            'duration':abs(video['duration']-plan.duration)<=tolerance,
            'start_zero':abs(video['start_time'])<=.001 and abs(audio['start_time'])<=.001,
            'av_alignment':drift<=tolerance,
            'faststart':'moov' in atoms and 'mdat' in atoms and atoms.index('moov')<atoms.index('mdat'),
            'real_inputs':all(s.video.mock is False and (not s.audio or s.audio.mock is False) for s in plan.segments)}
        if not all(checks.values()):
            raise ValueError('Final technical QC failed: '+','.join(k for k,v in checks.items() if not v))
        return {'passed':True,'checks':checks,'video':video,'audio':audio,'av_end_drift_seconds':drift,
                'tolerance_seconds':tolerance,'fps_tolerance':.0001,'frame_interval_tolerance_seconds':.00001,
                'size_bytes':path.stat().st_size}

    async def process(self, request, *, on_progress=None):
        request = await self.prepare_request(request)
        loop, event = asyncio.get_running_loop(), threading.Event()
        self._cancelled[request.request_id] = event
        async def notify(phase, fraction, frame, speed):
            if on_progress:
                response = on_progress(MediaProviderProgress(status=JobStatus.RUNNING,
                    activity=f'FFmpeg · {phase}'+(f' · frame {frame} · {speed}' if frame else ''),
                    remote_event='ffmpeg_progress', progress=fraction,progress_is_determinate=fraction is not None))
                if inspect.isawaitable(response): await response
        def emit(*args):
            with suppress(Exception): asyncio.run_coroutine_threadsafe(notify(*args),loop).result(timeout=5)
        worker = asyncio.create_task(asyncio.to_thread(self._render,request,event,emit))
        try:
            response = await asyncio.shield(worker)
            self._results[request.request_id] = response.result
            # Keep only bounded status evidence; artifacts hold durable history.
            while len(self._results)>32: self._results.pop(next(iter(self._results)))
            return response
        except asyncio.CancelledError:
            event.set()
            with suppress(Exception):
                response = await worker
                for payload in response.payloads: payload.content.close()
            raise
        except Exception as error:
            # Keep a bounded, path-free diagnostic, never raw FFmpeg command/stderr.
            if self.settings.post_temp_root:
                from movie_agent.media.post import fingerprint
                root=Path(self.settings.post_temp_root).resolve()/'diagnostics'
                root.mkdir(parents=True,exist_ok=True)
                identity=fingerprint({'request':request.request_id})
                (root/(identity+'.json')).write_text(json.dumps({'request_id':request.request_id,
                    'error_type':type(error).__name__,'provider':self.provider_id,'failed_at':utc_now().isoformat()}),encoding='utf-8')
                for old in sorted(root.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[16:]:
                    if old.resolve().parent==root:old.unlink()
            raise
        finally:
            self._cancelled.pop(request.request_id,None)
