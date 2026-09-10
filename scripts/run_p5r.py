"""Explicit authorized recovery of the existing P5 project; no baseline or audio restart."""
import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter
import sys

from movie_agent.domain import utc_now
from movie_agent.services.reference_recovery import ReferenceRecoveryProduction
from movie_agent.execution.durable_events import DurableLocalEventBus
from scripts.run_p5 import ROOT, SOURCE, compose, hashes, morning

async def run(fixture_path):
    fixture=json.loads(Path(fixture_path).read_text(encoding='utf-8'))
    PID=fixture['project_id']
    assert (ROOT/'project-id.txt').read_text().strip()==PID
    service,runtime,provider,settings=compose()
    started=perf_counter();old=hashes(SOURCE)
    try:
        def factory(pid):
            assert pid==PID, 'P5R is scoped to the existing authorized project'
            engine=ReferenceRecoveryProduction(ROOT/pid,provider,media_settings=settings,
                runtime_coordinator=runtime,event_bus=DurableLocalEventBus(ROOT/pid/'events'))
            def event(e):
                if e.event_type.value in {'artifact_created','job_failed','job_completed','node_started','node_failed','human_review_requested'}:
                    print(json.dumps({'event':e.event_type.value,'node':e.node_id,'shot':e.payload.get('shot_id')},ensure_ascii=False),flush=True)
            engine.event_bus.subscribe(event)
            return engine
        service.factory=factory
        engine=service.engine(PID)
        audio_before={a.uri:a.metadata.get('sha256') for a in engine.artifact_store.list_all()
                      if a.artifact_type.value=='audio' and a.provenance.provider_id in {'qwen3_tts','ace_step'}}
        prior_artifacts={(a.artifact_id,a.version):a.metadata.get('sha256') for a in engine.artifact_store.list_all()}
        if not (ROOT/'p5r-start.json').exists():
            (ROOT/'p5r-start.json').write_text(json.dumps({'project_id':PID,'started_at':utc_now().isoformat(),
                'prior_artifact_versions':len(prior_artifacts),'audio':audio_before,
                'old_compute_reservations':len(engine.media_runtime.quality_ledger.records()),
                'authorization_attachment':'05e964e0-b92c-44d4-a672-e36439b496ff/pasted-text.txt'},indent=2),encoding='utf-8')
        from movie_agent.quality.recovery import RecoveryPlan, HumanReferenceAcceptance, AcceptedReferenceBinding
        from movie_agent.media import MediaReference, ReferenceType
        from movie_agent.services.recovery_authorization import configure_quality_authorization, accept_reference_bundle
        saved=engine.artifact_store.get('recovery_plan')
        plan=RecoveryPlan.model_validate(engine.artifact_store.read_structured(saved) if saved else fixture['plan'])
        engine.configure_recovery(plan)
        auth=engine.artifact_store.read_structured(engine.artifact_store.get('p5r_recovery_authorization'))
        auth={**auth,'critic_config_revision':type(engine).critic_revision,'supersede_invalid_critic_reviews':True}
        configure_quality_authorization(engine,auth,user_statement=fixture['technical_supersession_user_reply'])
        accepted=fixture['human_acceptance']
        accepted_nodes={a.node_id for a in plan.reference_actions if a.subject_id==accepted['subject_id']}
        command=HumanReferenceAcceptance(acceptance_id=accepted['acceptance_id'],workflow_node_id=accepted['workflow_node_id'],
            user_statement=accepted['user_reply'],question=accepted['question'],bindings=[AcceptedReferenceBinding(
                subject_id=accepted['subject_id'],role=b['role'],known_limitations=b['known_limitations'],
                reference=MediaReference(reference_type=ReferenceType.CHARACTER,artifact_id=accepted['artifact_id'],
                    version=b['version'],sha256=b['sha256'],entity_id=accepted['subject_id'])) for b in accepted['bindings']],
            superseded_review_ids=[r.review_id for r in engine.human_gates.all() if r.node_id in accepted_nodes
                and r.status.value=='pending' and r.superseded_at is None])
        accept_reference_bundle(engine,command)
        engine.install_recovery()
        # Only explicitly authorized obsolete technical reviews above are superseded.
        # Every other outstanding review, including final aesthetics, remains paused.
        from movie_agent.domain import WorkflowNodeStatus, ReviewStatus
        for review in engine.human_gates.all():
            if review.superseded_at is None and review.status!=ReviewStatus.APPROVED:
                engine.current_production.set_status(review.node_id,WorkflowNodeStatus.WAITING_HUMAN)
        engine._durable_media_checkpoint()
        print('P5R RECOVER EXISTING PROJECT '+PID,flush=True)
        service.start(PID,resume=True)
        await service.tasks[PID]
        stats=morning(engine,runtime,perf_counter()-started,service.repository.get(PID).failure_code)
        stats['p5r']={'wall_this_process_seconds':perf_counter()-started,
            'critic_dispatches':engine.media_runtime.quality_ledger.critic_records(),
            'critic_outcomes':engine.media_runtime.quality_ledger.critic_outcomes(),
            'audio_reused':audio_before=={a.uri:a.metadata.get('sha256') for a in engine.artifact_store.list_all()
                if a.artifact_type.value=='audio' and a.provenance.provider_id in {'qwen3_tts','ace_step'}},
            'prior_versions_preserved':all(engine.artifact_store.get(aid,v).metadata.get('sha256')==digest for (aid,v),digest in prior_artifacts.items())}
        (ROOT/'p5r-acceptance.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'accepted':stats['accepted_shots'],'runtime':stats['runtime'],
            'failure':stats['failure'],'audio_reused':stats['p5r']['audio_reused']},ensure_ascii=False),flush=True)
        assert stats['p5r']['audio_reused']
        assert stats['p5r']['prior_versions_preserved']
        assert hashes(SOURCE)==old
    finally:
        await service.shutdown();runtime.close();await provider.aclose()


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser();parser.add_argument('--run-real',action='store_true')
    parser.add_argument('--fixture',default='tests/fixtures/hero_recovery.json')
    args=parser.parse_args()
    if not args.run_real:parser.error('Explicit --run-real required')
    asyncio.run(run(args.fixture))
