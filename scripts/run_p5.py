"""Explicit real P5 entrypoint. Existing DAG/P4A own dispatch; no fixed GPU sequence."""
import argparse
import asyncio
from hashlib import sha256
import html
import json
from pathlib import Path
import shutil
from time import perf_counter

from movie_agent.application.service import ProductionService
from movie_agent.config import LLMConfig
from movie_agent.domain import ProjectBrief,QualityProfile
from movie_agent.execution.durable_events import DurableLocalEventBus
from movie_agent.execution.jobs import JobManager
from movie_agent.model_services.wiring import build_spark_runtime
from movie_agent.providers.registry import MediaProviderSettings
from movie_agent.providers.openai_compatible import OpenAICompatibleLLMProvider
from movie_agent.services.hero_production import HeroMovieProduction
from movie_agent.services.reasoning_production import ReasoningMovieProduction
from movie_agent.storage.projects import LocalProjectRepository
from movie_agent.quality.reports import aggregate_shot,aggregate_film
from movie_agent.quality.cinematic import evaluate_cinematic,film_proxy

ROOT=Path('workspace/p5-hero-film')
SOURCE=Path('workspace/p4d-audio-acceptance/project_3effeb45f3844bf89c2c96e83770114b')


def hashes(path):
    return {str(p.relative_to(path)):sha256(p.read_bytes()).hexdigest() for p in path.rglob('*') if p.is_file()}


async def baseline(provider,runtime,settings):
    target=ROOT/'baseline'
    if not target.exists(): shutil.copytree(SOURCE,target)
    engine=ReasoningMovieProduction(target,provider,media_settings=settings,runtime_coordinator=runtime,
        event_bus=DurableLocalEventBus(target/'p5-events'))
    project,graph=engine._restore(); engine.current_project,engine.current_production=project,graph
    engine.job_manager=JobManager(engine.event_bus,engine.trace_id);engine.media_runtime.bind_jobs(engine.job_manager)
    reports=[]
    visual_evidence={}
    for shot in project.shots:
        print('BASELINE VLM '+shot.shot_id,flush=True)
        visual_evidence[shot.shot_id]=await engine._vision_evaluation(project,shot,engine._latest_video(shot.shot_id))
    for shot in project.shots:
        video=engine._latest_video(shot.shot_id)
        visual=visual_evidence[shot.shot_id]
        print('BASELINE CINEMATIC '+shot.shot_id,flush=True)
        cinematic=await evaluate_cinematic(engine,project,shot=shot,artifact=video)
        technical=engine.technical_qc.evaluate(shot,video)
        with engine.binary_store.open(video.uri) as stream: digest=sha256(stream.read()).hexdigest()
        current=video.model_copy(update={'metadata':{**video.metadata,'sha256':digest}})
        reports.append(aggregate_shot(shot,current,[visual,cinematic,technical],engine.media_runtime.inspections))
    report=aggregate_film(project.project_id,reports)
    proxy=film_proxy(project,reports,engine.timeline,engine.media_runtime.inspections)
    proxy['rough_cut_version']=engine.artifact_store.get('rough_cut').version
    film_critic=await evaluate_cinematic(engine,project,proxy=proxy)
    report.cinematic_evaluation_id=film_critic.evaluation_id
    prior=engine.artifact_store.get('p5_baseline_film_quality')
    if not prior:
        engine.artifact_store.create_structured('p5_baseline_film_quality',report.model_dump(mode='json'),
            metadata={'purpose':'film_quality_report','baseline_source':str(SOURCE),'source_runtime_seconds':30,
                      'human_audio_feedback':'native overlap and immature Ella timbre; unresolved in source'})
    (ROOT/'baseline-quality.json').write_text(report.model_dump_json(indent=2),encoding='utf-8')
    return project


def brief_from(seed):
    return ProjectBrief(title='悬浮症 · P5 Hero Film',logline='一次悬浮装置故障，迫使一个人面对脚下的世界与自己相信的恐惧。',
        story_description=seed.brief.story_description+'\nCreative seed: '+seed.story_bible.synopsis,
        target_duration=105,output_language='zh-CN',genre=['psychological science fiction'],desired_character_count=2,
        character_descriptions=[c.model_dump_json() for c in seed.characters],locations=[l.description for l in seed.locations],
        world_rules=seed.story_bible.world_facts,max_shots=12,max_retry=1,quality_level=QualityProfile.SHOWCASE,
        resolution='1920x1080',fps=24,dialogue_density=.2,visual_style=seed.visual_bible.style_statement,
        palette=seed.visual_bible.palette,voice_style='Mature adult voices, restrained natural Mandarin cinematic delivery.',
        music_style='Instrumental, no vocals, no lyrics. A recurring restrained three-note analog synth motif, low pulse and soft piano. '
                    'Preserve motif across scenes; vary intensity with the emotional arc.',
        user_constraints=['Create exactly 3 scenes and 12 shots, 4 shots per scene, 90–120 seconds total. '
                          'Each scene approximately 35 seconds. Mix shot durations 4–6, 7–10, 10–15 seconds; no shot over 15 seconds.',
            'Use 2 recurring adult characters and a small number of consistent locations. Prefer single-character shots, '
            'reaction shots, environmental inserts, profile, over-the-shoulder and off-screen dialogue. '
            'Do not introduce additional speaking characters.',
            'Every shot must reveal new story information and have an emotional function: beginning, conflict, reversal, ending. '
            'Avoid duplicated narrative purpose and montage without causality.',
            'No lip-sync model exists. Keep spoken lines short enough for the shot. Avoid frontal close-ups with long dialogue; '
            'make dialogue audibly off-screen or use back/profile/reaction framing. Exact canonical dialogue will use TTS.',
            'Maintain adult character faces, wardrobe, location landmarks and light palette across all shots. '
            'Use English visual-generation descriptions and natural Chinese dialogue. No text overlays or letterboxing in generated frames.'])


def compose():
    from movie_agent.application.runtime_configuration import production_media_settings
    settings=production_media_settings(ROOT,profile='installed')
    provider=OpenAICompatibleLLMProvider(LLMConfig.from_env())
    async def diagnose_http(response):
        if response.status_code<400: return
        await response.aread()
        try:
            body=response.json()
            # Keep only the serving error, never request headers or model reasoning.
            detail=body.get('error',body.get('detail','unknown serving error'))
            if isinstance(detail,dict):detail={k:detail[k] for k in ('type','message','code') if k in detail}
        except ValueError:detail='non-JSON error response'
        (ROOT/'reasoning-http-diagnostic.json').write_text(json.dumps({'status':response.status_code,'error':detail},
            ensure_ascii=False,indent=2),encoding='utf-8')
    provider._client.event_hooks['response']=[diagnose_http]
    runtime=build_spark_runtime(provider.config,settings)
    def factory(pid):
        engine=HeroMovieProduction(ROOT/pid,provider,media_settings=settings,runtime_coordinator=runtime,
            event_bus=DurableLocalEventBus(ROOT/pid/'events'))
        engine.event_bus.subscribe(lambda e: print(json.dumps({'event':e.event_type.value,'node':e.node_id,
            'shot':e.payload.get('shot_id')},ensure_ascii=False),flush=True) if e.event_type.value in
            {'artifact_created','job_failed','job_completed','node_started','node_failed','human_review_requested'} else None)
        return engine
    return ProductionService(LocalProjectRepository(ROOT),factory),runtime,provider,settings


def morning(engine,runtime,elapsed,failure=None):
    from movie_agent.domain import utc_now
    project=engine.current_project
    artifacts=engine.artifact_store.list_all()
    quality=engine.artifact_store.get('film_quality_report')
    report=engine.artifact_store.read_structured(quality) if quality else None
    candidates=[a for a in artifacts if a.artifact_id=='technical_candidate_final']
    candidate=candidates[-1] if candidates else None
    reservations=engine.media_runtime.quality_ledger.records()
    generations={kind:sum(r['kind']==kind for r in reservations) for kind in ['video_regenerate','frame_edit','vlm_inspection','tts','music']}
    h3=[a for a in artifacts if a.artifact_type.value=='video' and a.provenance.provider_id=='comfyui-video']
    stats={'project_id':project.project_id,'runtime':candidate.metadata.get('duration_seconds') if candidate else None,
        'scene_count':len(project.scenes),'shot_count':len(project.shots),'accepted_shots':report['accepted_shots'] if report else 0,
        'generations':generations,'flux_outputs':sum(a.provenance.provider_id=='flux_direct' and a.artifact_type.value=='frame' for a in artifacts),
        'h3_outputs':len(h3),'h3_repairs':sum(a.version>1 for a in h3),
        'video_generations_per_accepted_shot':generations['video_regenerate']/report['accepted_shots'] if report and report['accepted_shots'] else None,
        'elapsed_wall_seconds':(utc_now()-project.created_at).total_seconds(),'elapsed_last_run_seconds':elapsed,
        'failure':failure,'human_listening':'pending','human_aesthetically_approved':False,
        'quality':report,'final':candidate.model_dump(mode='json') if candidate else None,
        'resources':runtime.view(),'human_items':[engine.artifact_store.read_structured(a) for a in artifacts
                                               if a.selected and a.metadata.get('purpose')=='p5_shot_human_review']}
    project_jobs={j.job_id for j in engine._all_jobs()}|{a.source_job_id for a in artifacts if a.source_job_id}
    history=runtime.state_path.with_suffix('.history.jsonl') if runtime.state_path else None
    entries=[json.loads(line) for line in history.read_text(encoding='utf-8').splitlines() if line.strip()] if history and history.exists() else []
    decisions=[e['value'] for e in entries if e['kind']=='decision'] if entries else [d.model_dump(mode='json') for d in runtime.decisions]
    decisions=[d for d in decisions if d['project_id']==project.project_id]
    project_jobs|={d['job_id'] for d in decisions}
    observations=[e['value'] for e in entries if e['kind']=='observation'] if entries else [o.model_dump(mode='json') for o in runtime.observations]
    stats['resources']['observations']=[o for o in observations if o['job_id'] in project_jobs]
    stats['resources']['decisions']=decisions
    stats['resources']['history_source']=str(history) if entries else 'in-memory rolling window'
    stats['scope']='technical AI acceptance; human listening/aesthetic approval pending'
    stats['thresholds']=engine.thresholds.model_dump(mode='json')
    stats['compute_budget']=engine.media_runtime.quality_ledger.budget.model_dump(mode='json')
    baseline_file=ROOT/'baseline-quality.json'
    stats['baseline_quality']=json.loads(baseline_file.read_text(encoding='utf-8')) if baseline_file.exists() else None
    accepted_ids={r['shot_id'] for r in report['shots'] if r['status']=='accepted'} if report else set()
    stats['human_items']=[r for r in stats['human_items'] if r['shot_id'] not in accepted_ids]
    stats['shot_status_counts']={'accepted':len(accepted_ids),
        'rejected':sum(r['status']=='rejected' for r in report['shots']) if report else 0,
        'human_review':len(stats['human_items'])}
    ledger=engine.media_runtime.quality_ledger
    if engine.artifact_store.get('p5r_recovery_authorization'):
        stats['p5r_critic_dispatches']=ledger.critic_records()
        stats['p5r_critic_outcomes']=ledger.critic_outcomes()
        stats['generations']['vlm_inspection']+=len(stats['p5r_critic_dispatches'])
    (ROOT/'acceptance.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
    base=f'http://127.0.0.1:8089/projects/{project.project_id}/artifacts/'
    def link(aid,version,label):return f'<a href="{base}{aid}/versions/{version}/download">{html.escape(label)}</a>'
    links=[]
    if candidate:
        links.append(link(candidate.artifact_id,candidate.version,'播放 / 下载 MP4'))
        for stem in candidate.metadata.get('audio_stems',[]):links.append(link(stem['artifact_id'],stem['version'],stem['name']))
        subtitle=candidate.metadata.get('subtitle_artifact')
        if subtitle:links.append(link(subtitle['artifact_id'],subtitle['version'],'SRT'))
        links.append(link(candidate.metadata['manifest_artifact_id'],1,'Render Manifest'))
    if quality:links.append(link(quality.artifact_id,quality.version,'Film Quality Report'))
    pack=engine.artifact_store.get('reference_identity_set')
    if pack:links.append(link(pack.artifact_id,pack.version,'Reference Identity Set'))
    audio_assets=[a for a in artifacts if a.selected and a.artifact_type.value=='audio'
                  and (a.artifact_id.startswith('speech_') or a.artifact_id.startswith('music_'))]
    audio_html=''.join(f'<div><p>{link(a.artifact_id,a.version,a.artifact_id)}</p>'
        f'<audio controls preload="none" src="{base}{a.artifact_id}/versions/{a.version}/download"></audio></div>' for a in audio_assets)
    reference_assets=[a for a in artifacts if a.artifact_id.startswith('identity_') and a.artifact_type.value=='frame']
    reference_html=''.join(f'<figure><img loading="lazy" style="width:100%" src="{base}{a.artifact_id}/versions/{a.version}/download">'
        f'<figcaption>{link(a.artifact_id,a.version,a.artifact_id+" v"+str(a.version))}</figcaption></figure>' for a in reference_assets)
    rows=''.join(f'<tr><td>{html.escape(s.shot_id)}</td><td>{s.duration_seconds:g}s</td><td>{html.escape(s.narrative.purpose)}</td></tr>' for s in project.shots)
    shot_cards=[]
    by_shot={r['shot_id']:r for r in report['shots']} if report else {}
    for shot in project.shots:
        current=by_shot.get(shot.shot_id)
        frame=engine.artifact_store.get('frame_'+shot.shot_id+'_first')
        video=engine.artifact_store.get('video_'+shot.shot_id)
        if not frame and not video:continue
        state=current['status'] if current else 'quality evidence pending'
        still=(f'<img style="width:100%" loading="lazy" src="{base}{frame.artifact_id}/versions/{frame.version}/download">' if frame else '')
        motion=(f'<video style="width:100%" controls preload="none" src="{base}{video.artifact_id}/versions/{video.version}/download"></video>' if video else '<p>视频待生成</p>')
        versions=[a for a in artifacts if a.artifact_id in {'video_'+shot.shot_id,'frame_'+shot.shot_id+'_first','frame_'+shot.shot_id+'_last'}]
        history=' · '.join(link(a.artifact_id,a.version,a.artifact_id+' v'+str(a.version)) for a in versions)
        issues=[i.message for ins in engine.media_runtime.inspections if video and ins.target_artifact_id==video.artifact_id
                and ins.target_artifact_version==video.version for i in ins.issues]
        repair=[engine.artifact_store.read_structured(a) for a in artifacts if a.selected and
                (a.artifact_id.startswith('p5r_frame_repair_'+shot.shot_id) or a.artifact_id.startswith('repair_cost_'+shot.shot_id))]
        scores=f"视觉 {current['visual_score']} / cinematic {current['cinematic_score']}" if current else '尚无已提交视频分数'
        shot_cards.append(f'<article><h3>{html.escape(shot.shot_id)} · {shot.duration_seconds:g}s · {html.escape(state)}</h3>'
            f'<p>{html.escape(scores)}</p><div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">{still}{motion}</div>'
            f'<p>{html.escape("; ".join(issues))}</p><details><summary>版本与修复原因</summary>{history}'
            f'<pre>{html.escape(json.dumps(repair,ensure_ascii=False,indent=2))}</pre></details></article>')
    issue_rows=[]
    offsets={c.shot_id:c.start_time_seconds for track in engine.timeline.video_tracks for c in track.clips} if engine.timeline else {}
    for inspection in engine.media_runtime.inspections:
        for issue in inspection.issues:
            if inspection.shot_id not in offsets:continue
            ranges=', '.join(f'{offsets[inspection.shot_id]+r.start_seconds:.1f}–{offsets[inspection.shot_id]+r.end_seconds:.1f}s'
                for r in issue.time_ranges) or '镜头范围；未定位到具体时间'
            issue_rows.append(f'<tr><td>{html.escape(inspection.shot_id)}</td><td>{html.escape(ranges)}</td>'
                f'<td>{html.escape(issue.severity.value)}</td><td>{html.escape(issue.message)}</td></tr>')
    preview=(f'<video controls preload="metadata" style="width:100%" src="{base}{candidate.artifact_id}/versions/{candidate.version}/download"></video>'
             if candidate else '')
    compact={k:v for k,v in stats.items() if k not in {'quality','final','resources'}}
    outcome=('技术候选片已生成；人工审美与试听待确认。' if candidate else
             'P5 成片目标尚未达成：当前没有通过全部技术质量门禁的候选片。以下是实际生成的参考图、音频和失败证据。')
    memory={}
    for observation in stats['resources']['observations']:
        sid=observation['service_id'];row=memory.setdefault(sid,{'jobs':0,'execution_seconds':0,'warmup_seconds':0,'sampled_peak_bytes':None})
        row['jobs']+=1;row['execution_seconds']+=observation['execution_seconds'];row['warmup_seconds']+=observation['warmup_seconds']
        measured=observation['observed_peak_bytes']
        if measured is not None:row['sampled_peak_bytes']=max(row['sampled_peak_bytes'] or 0,measured)
    compact['resource_summary']=memory
    text=f'''<!doctype html><html lang="zh"><meta charset="utf-8"><title>P5 Morning Review</title>
<style>body{{font:17px/1.7 system-ui;max-width:1100px;margin:40px auto;padding:20px;background:#15171b;color:#eee}}a{{color:#a9d4ff;margin-right:20px;overflow-wrap:anywhere}}td{{padding:8px;border-bottom:1px solid #444}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}figure{{margin:0}}figcaption{{font-size:13px;overflow-wrap:anywhere}}audio{{width:100%}}</style>
<h1>P5 Morning Review</h1><p>{html.escape(project.brief.title)}</p><p>{outcome}</p><p>人工审美与试听待确认；没有伪造人工批准。</p>
<p>{' · '.join(links) or '尚无可导出的候选片'}</p><p>已通过镜头时长：{report['accepted_seconds'] if report else 0} 秒 · 合格镜头：{stats['accepted_shots']} · 候选片：{str(stats['runtime'])+' 秒' if stats['runtime'] is not None else '未生成'}</p>
<p>Accepted {stats['shot_status_counts']['accepted']} / Rejected {stats['shot_status_counts']['rejected']} / Human review {stats['shot_status_counts']['human_review']}</p>
{preview}
<p>{html.escape(project.story_bible.synopsis if project.story_bible else project.brief.story_description)}</p>
<h2>镜头实物与质量</h2>{''.join(shot_cards) or '<p>尚无镜头帧或视频产物</p>'}
<h2>实际音频（试听待确认）</h2>{audio_html or '<p>尚无音频产物</p>'}
<details><summary>参考图版本与失败历史（生成成功不代表质量通过）</summary><div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px">{reference_html}</div></details>
<h2>镜头计划</h2><table>{rows}</table><h2>观察问题（近似片中时间）</h2><table>{''.join(issue_rows)}</table><h2>待确认与资源证据</h2><p><a href="acceptance.json">完整验收与遥测 JSON</a></p><details><summary>详细记录</summary><pre>{html.escape(json.dumps(compact,ensure_ascii=False,indent=2))}</pre></details></html>'''
    (ROOT/'morning-review.html').write_text(text,encoding='utf-8')
    return stats


async def run(args):
    ROOT.mkdir(parents=True,exist_ok=True)
    service,runtime,provider,settings=compose(); started=perf_counter(); original=hashes(SOURCE)
    try:
        pidfile=ROOT/'project-id.txt'
        if pidfile.exists(): pid=pidfile.read_text().strip();engine=service.engine(pid)
        else:
            seed=await baseline(provider,runtime,settings)
            record=service.create(brief_from(seed));pid=record.project.project_id;engine=service.engine(pid)
            pidfile.write_text(pid,encoding='utf-8')
        print('P5 HERO PROJECT '+pid,flush=True)
        if args.serve:
            import uvicorn
            from movie_agent.api.app import create_app
            await uvicorn.Server(uvicorn.Config(create_app(service,resource_runtime=runtime),host='127.0.0.1',port=8089)).serve()
            return
        # Application calls existing DAG. It never resolves human gates on behalf of the user.
        service.start(pid,resume=service.repository.get(pid).status.value!='created')
        await service.tasks[pid]
        stats=morning(engine,runtime,perf_counter()-started,service.repository.get(pid).failure_code)
        print(json.dumps({k:stats[k] for k in ('project_id','runtime','accepted_shots','failure','generations')},ensure_ascii=False),flush=True)
        assert hashes(SOURCE)==original,'Old acceptance project was modified'
    finally:
        await service.shutdown();runtime.close();await provider.aclose()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run-real',action='store_true');parser.add_argument('--serve',action='store_true')
    args=parser.parse_args()
    if not args.run_real:parser.error('Explicit --run-real opt-in required')
    asyncio.run(run(args))
