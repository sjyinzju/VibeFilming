"""Decoder evidence for shot order, cut boundaries and original native audio."""
from array import array
import json
import math
from pathlib import Path
import re
import subprocess

from scripts.accept_post import ROOT,SOURCE,service_at


def ffmpeg(args):
    return subprocess.run(['ffmpeg','-hide_banner','-nostdin','-loglevel','info',*args],
                          capture_output=True,check=True,timeout=120)


def verify():
    report=json.loads((ROOT/'acceptance.json').read_text(encoding='utf-8'))
    engine=service_at().engine(SOURCE.name)
    final=ROOT/'final_film.mp4'
    rows=[]
    for segment in report['final']['metadata']['render_plan']['segments']:
        def path(ref):
            return engine.binary_store.describe(engine.artifact_store.get(ref['artifact_id'],ref['version']).uri).path
        video=path(segment['video']);audio=path(segment['audio'])
        samples=[]
        # Includes both frames adjacent to the hard cut; independently decode sources and export.
        for offset in [0, .25,7.5,14.75,359/24]:
            def frame(file,time):
                return ffmpeg(['-ss',str(time),'-i',str(file),'-frames:v','1','-vf','scale=128:72',
                               '-f','rawvideo','-pix_fmt','rgb24','pipe:1']).stdout
            expected=frame(video,offset);actual=frame(final,segment['start']+offset)
            assert len(expected)==len(actual)==128*72*3
            error=sum(abs(a-b) for a,b in zip(expected,actual))/(255*len(actual))
            assert error<.025, 'Unexpected frame order or boundary gap'
            samples.append({'time':segment['start']+offset,'normalized_mean_absolute_error':error})
        def pcm(file,start):
            data=ffmpeg(['-ss',str(start),'-i',str(file),'-t','15','-vn','-ar','8000','-ac','1',
                         '-f','f32le','pipe:1']).stdout
            values=array('f');values.frombytes(data);return values
        original=pcm(audio,0);rendered=pcm(final,segment['start'])
        n=min(len(original),len(rendered))
        # Skip AAC encoder boundary priming; compare 0.1–14.9 s waveform shape.
        left=original[800:n-800];right=rendered[800:n-800]
        correlation=sum(a*b for a,b in zip(left,right))/math.sqrt(sum(a*a for a in left)*sum(b*b for b in right))
        assert correlation>.90,'Native audio waveform does not match source'
        rows.append({'shot_id':segment['shot_id'],'frame_comparisons':samples,
                     'native_audio_correlation':correlation,'audio_window':'0.1–14.9 s at 8 kHz mono'})
    log=ffmpeg(['-i',str(final),'-vn','-af','loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json','-f','null','-']).stderr.decode()
    loudness=json.loads(re.findall(r'\{\s*"input_i"[\s\S]*?\}',log)[-1])
    assert float(loudness['input_tp'])<=-1 and abs(float(loudness['input_i'])+16)<=1.5
    output={'shot_order_and_boundaries':rows,'decoded_final_audio_loudness':loudness,
            'scope':'Sampled frame equality and source waveform comparison; no aesthetic or speech alignment claim.',
            'upstream_inference_calls':0}
    (ROOT/'decoded-verification.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
    print(json.dumps(output,indent=2))


if __name__=='__main__':verify()
