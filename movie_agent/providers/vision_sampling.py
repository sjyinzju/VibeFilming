"""Deterministic media probing and bounded targeted samples at the provider boundary."""

import asyncio
from fractions import Fraction
import json
from pathlib import Path
import tempfile

from movie_agent.domain import ProviderErrorType
from movie_agent.providers.base import ProviderFailure


async def _run(args, *, timeout=60):
    process=await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        output,_=await asyncio.wait_for(process.communicate(),timeout)
    except BaseException:
        if process.returncode is None:process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise ProviderFailure("inspection media decoding failed",ProviderErrorType.MEDIA_CORRUPT)
    return output


async def probe_video(content):
    """Read actual stream metadata, rather than trusting artifact JSON duration."""
    try:
        with tempfile.TemporaryDirectory(prefix='vlm-probe-') as directory:
            source=Path(directory)/'input.mp4';source.write_bytes(content)
            output=await _run(['ffprobe','-v','error','-select_streams','v:0','-show_entries',
                'stream=duration,avg_frame_rate,nb_frames,width,height:format=duration','-of','json',str(source)])
        data=json.loads(output);stream=data['streams'][0]
        duration=float(stream.get('duration') or data['format']['duration'])
        fps=float(Fraction(stream['avg_frame_rate']))
        count=int(stream.get('nb_frames') or round(duration*fps))
        if duration<=0 or fps<=0 or count<1:raise ValueError('invalid stream metadata')
        return {'duration_seconds':duration,'fps':fps,'frame_count':count,'width':stream['width'],'height':stream['height']}
    except (OSError,KeyError,ValueError,TimeoutError) as error:
        raise ProviderFailure("ffprobe cannot validate inspection video",ProviderErrorType.MEDIA_CORRUPT) from error


def uniform_indices(frame_count, count):
    count=min(frame_count,count)
    # Matches the probed vLLM OpenCV np.linspace(..., dtype=int) algorithm.
    return [int(i*(frame_count-1)/(count-1)) for i in range(count)] if count>1 else [0]


async def extract_video_frames(content, indices, *, width=640):
    """Decode exactly the planned source indices; this never generates new media content."""
    if not indices or len(indices) > 48 or indices != sorted(set(indices)) or indices[0] < 0:
        raise ProviderFailure("invalid inspection frame indices", ProviderErrorType.INVALID_REQUEST)
    try:
        with tempfile.TemporaryDirectory(prefix='vlm-sequence-') as directory:
            source=Path(directory)/'input.mp4';source.write_bytes(content)
            selection='+'.join(f'eq(n\\,{i})' for i in indices)
            await _run(['ffmpeg','-v','error','-i',str(source),'-vf',f'select={selection},scale={width}:-2',
                '-fps_mode','passthrough','-frames:v',str(len(indices)),'-c:v','mjpeg','-q:v','3',
                '-threads','1',str(Path(directory)/'frame-%04d.jpg')])
            frames=[p.read_bytes() for p in sorted(Path(directory).glob('frame-*.jpg'))]
            if len(frames)!=len(indices):
                raise ValueError('source frame count disagrees with decoded samples')
            return frames
    except (OSError,ValueError,TimeoutError) as error:
        raise ProviderFailure("inspection frame extraction failed",ProviderErrorType.MEDIA_CORRUPT) from error


async def targeted_samples(content, ranges, *, max_frames=8, last_frame_timestamp=None):
    """Extra local evidence, labelled in original source time, never a new target."""
    times=sorted({round(t.start_seconds+(t.end_seconds-t.start_seconds)*fraction,6)
                  for t in ranges for fraction in (0,.25,.5,.75,1)})
    if last_frame_timestamp is not None:
        times = sorted({min(t, last_frame_timestamp) for t in times})
    if len(times)>max_frames:
        times=[times[i] for i in uniform_indices(len(times),max_frames)]
    samples=[]
    try:
        with tempfile.TemporaryDirectory(prefix='vlm-targeted-') as directory:
            source=Path(directory)/'input.mp4';source.write_bytes(content)
            for timestamp in times:
                output=await _run(['ffmpeg','-v','error','-ss',str(timestamp),'-i',str(source),'-frames:v','1',
                    '-vf','scale=640:-2','-f','image2pipe','-c:v','mjpeg','-threads','1','pipe:1'])
                if not output:raise ValueError('target sample outside decodable timeline')
                samples.append((timestamp,output))
    except (OSError,ValueError,TimeoutError) as error:
        raise ProviderFailure("targeted sampling failed",ProviderErrorType.MEDIA_CORRUPT) from error
    return samples
