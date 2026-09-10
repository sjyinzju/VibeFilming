"""Deterministic FFmpeg execution graph for three existing Timeline audio tracks."""
from movie_agent.media.post import ExactPostSource


def duck_expression(intervals, depth_db, attack, release):
    envelopes = [f'if(lt(t,{start}),max(0,(t-({start}-{attack}))/{attack}),if(lte(t,{end}),1,max(0,1-(t-{end})/{release})))'
                 for start, end in intervals]
    amount = '0'
    for envelope in envelopes:
        amount = f'max({amount},{envelope})'
    return f'pow(10,({depth_db})*({amount})/20)'


def render_stems(provider, plan, work, event, emit):
    """Render full-length stems once, then deterministic mix before P4C loudnorm."""
    total = sum(s.audio_samples for s in plan.segments)
    dialogue = [(l['start'], l['start']+l['duration']) for l in plan.audio_layers
                if l['purpose']=='speech' and l['enabled']]
    groups = [('native_sound', 'generated_native_audio', 'native_audio_duck_db'),
              ('dialogue', 'speech', None), ('music', 'music', 'music_duck_db')]
    stems = []
    for label, purpose, duck in groups:
        layers = [l for l in plan.audio_layers if l['purpose']==purpose and l['enabled']]
        argv = provider._base()
        filters, pads = [], []
        for i, layer in enumerate(layers):
            argv += ['-i',str(provider._path(ExactPostSource.model_validate(layer['source']),plan.project_id))]
            content_samples = round(layer['duration']*48000)
            chain = (f'[{i}:a:0]asetpts=PTS-STARTPTS,atrim=start={layer["source_in"]}:duration={layer["duration"]},'
                'asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,'
                f'volume={layer["gain_db"]}dB,apad,atrim=end_sample={content_samples}')
            if layer['fade_in']:
                chain += f',afade=t=in:d={layer["fade_in"]}'
            if layer['fade_out']:
                chain += f',afade=t=out:st={max(0,layer["duration"]-layer["fade_out"])}:d={layer["fade_out"]}'
            chain += f',adelay={round(layer["start"]*48000)}S:all=1,apad,atrim=end_sample={total}[a{i}]'
            filters.append(chain)
            pads.append(f'[a{i}]')
        if pads:
            filters.append(''.join(pads)+f'amix=inputs={len(pads)}:normalize=0:duration=longest[m]')
        else:
            filters.append(f'anullsrc=r=48000:cl=stereo,atrim=end_sample={total}[m]')
        tail='[m]anull'
        if duck and dialogue:
            expr=duck_expression(dialogue,plan.audio_policy[duck],plan.audio_policy['audio_duck_attack_seconds'],plan.audio_policy['audio_duck_release_seconds'])
            tail=f"[m]volume='{expr}':eval=frame"
        filters.append(tail+f',apad,atrim=end_sample={total},asetpts=N/SR/TB[out]')
        path=work/(label+'.wav')
        provider._run(argv+['-filter_complex',';'.join(filters),'-map','[out]','-c:a','pcm_f32le',str(path)],
                      work,event,emit,'mix stem '+label,plan.duration)
        stems.append((label,path))
    mixed=work/'mixed.wav'
    argv=provider._base()
    for _, path in stems: argv+=['-i',str(path)]
    provider._run(argv+['-filter_complex',f'[0:a][1:a][2:a]amix=inputs=3:normalize=0,atrim=end_sample={total}[a]',
        '-map','[a]','-c:a','pcm_f32le',str(mixed)],work,event,emit,'mix three tracks',plan.duration)
    return stems,mixed
