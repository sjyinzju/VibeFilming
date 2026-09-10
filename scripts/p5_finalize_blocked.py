"""Close the exhausted P5 run honestly; no AI dispatch or human approval."""
import asyncio
import json
from collections import Counter
from pathlib import Path
import sys

from scripts.run_p5 import ROOT, SOURCE, compose, hashes, morning
from movie_agent.application.repository import ProductionStatus


async def main():
    service,runtime,provider,_=compose()
    original=hashes(SOURCE)
    try:
        pid=(ROOT/'project-id.txt').read_text().strip()
        engine=service.engine(pid)
        assert not engine.artifact_store.get('technical_candidate_final')
        assert not runtime.active_leases(), 'Do not stop services with outstanding work'
        await engine._execute_node('audio_post',engine.current_project,engine.current_production,auto_approve=False)
        record=service.repository.get(pid)
        record.status=ProductionStatus.WAITING_HUMAN
        record.failure_code='REFERENCE_QUALITY_BUDGET_EXHAUSTED'
        service.repository.save(record)
        for model in runtime.manager.all():
            await model.status()
        runtime.set_ready_jobs([])
        runtime.settings.warm_idle_ttl=0
        await runtime.maintain(pid)
        await runtime.snapshot(pid)
        cleanup={'services':{s.descriptor.service_id:s.descriptor.status.value for s in runtime.manager.all()},
                 'active_leases':len(runtime.active_leases()),'method':'P4A idle TTL eviction; no model generation'}
        (ROOT/'final-cleanup.json').write_text(json.dumps(cleanup,indent=2),encoding='utf-8')
        stats=morning(engine,runtime,0,record.failure_code)
        pack=engine.artifact_store.read_structured(engine.artifact_store.get('reference_identity_set'))
        references={}
        for ref in pack['references']:
            if ref['subject_id'] not in references:
                references[ref['subject_id']]={k:ref.get(k) for k in ('artifact_id','version','sha256','selected','quality_status','inspection_result_id')}
        for ref in references.values():
            inspection=next((i for i in engine.media_runtime.inspections if i.result_id==ref['inspection_result_id']),None)
            if inspection:
                ref['scores']=[s.model_dump(mode='json') for s in inspection.scores]
                ref['issues']=[i.model_dump(mode='json') for i in inspection.issues]
        entries=[json.loads(line) for line in runtime.state_path.with_suffix('.history.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
        starts=Counter(e['value']['service_id'] for e in entries if e['kind']=='service_status' and e['value']['project_id']==pid and e['value']['status']=='starting')
        observations=stats['resources']['observations']
        switches=sum(a['service_id']!=b['service_id'] for a,b in zip(observations,observations[1:]))
        stats.update(status=record.status.value,planned_seconds=sum(s.duration_seconds for s in engine.current_project.shots),
            reference_results=references,reference_pass_count=sum(r['selected'] for r in references.values()),
            resource_summary={'model_start_transitions':dict(starts),'completed_observation_service_transitions':switches,
                'transition_definition':'Changes between consecutive project-scoped completed resource observations; not concurrent residency changes.',
                'sampled_peak_unified_pressure_bytes':max((o['observed_peak_bytes'] or 0 for o in observations),default=0),
                'total_observed_execution_seconds':sum(o['execution_seconds'] for o in observations),
                'total_observed_warmup_seconds':sum(o['warmup_seconds'] for o in observations),
                'confirmed_oom_events':1 if (ROOT/'flux-startup-failure.json').exists() else 0},
            cleanup=cleanup,old_source_hashes_unchanged=hashes(SOURCE)==original,
            limitations=['No accepted video, MP4, SRT, final stems or render manifest.',
                        'Reference budget is exhausted. No further automatic reference reviews authorized by this run policy.',
                        'Human listening and aesthetics have not been approved.'])
        (ROOT/'acceptance.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:stats[k] for k in ('project_id','status','reference_pass_count','reference_results','resource_summary','cleanup')},ensure_ascii=False,indent=2))
        assert hashes(SOURCE)==original
    finally:
        await service.shutdown()
        runtime.close()
        await provider.aclose()


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
