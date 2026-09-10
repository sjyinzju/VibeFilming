"""Read-only compact recovery evidence, without loading credential configuration."""
import json
from pathlib import Path
from movie_agent.artifacts import LocalArtifactStore


def main(compact=False):
    root=Path('workspace/p5-hero-film')
    store=LocalArtifactStore(root/(root/'project-id.txt').read_text().strip()/'artifacts')
    def data(identity):
        artifact=store.get(identity)
        return store.read_structured(artifact) if artifact else None
    pack=data('reference_identity_set') or {}
    refs=[{k:r.get(k) for k in ('subject_id','reference_role','version','selected','quality_status','human_review_id','known_limitations')}
        for r in pack.get('references',[])]
    outcomes=[store.read_structured(a) for a in store.list_versions('p5r_critic_outcome')]
    pending=[]
    for o in outcomes[-3:]:
        pending.append({k:o.get(k) for k in ('subject','version','critic_config_revision','outcome','diagnostic')})
        pending[-1]['attempts']=[{k:a.get(k) for k in ('finish_reason','validation_diagnostic','latency_seconds')} for a in o.get('provider_attempts',[])]
    film=data('film_quality_report') or {}
    state=json.loads(Path('workspace/_resource_runtime/spark-state.json').read_text(encoding='utf-8'))
    active=[{k:l.get(k) for k in ('job_id','service_id','status','acquired_at')} for l in state.get('leases',[]) if l.get('released_at') is None]
    artifacts=store.list_all()
    result={'references':refs,'critic_outcomes':len(outcomes),'latest_outcomes':pending,
        'accepted':film.get('accepted_shots',0),'seconds':film.get('accepted_seconds',0),'active':active,
        'frames':[{ 'id':a.artifact_id,'v':a.version,'provider':a.provenance.provider_id,
            'conditioning':a.provenance.parameters.get('reference_conditioning',{}).get('transport')
                if a.provenance.parameters.get('reference_conditioning') else None}
            for a in artifacts if a.artifact_type.value in {'image','frame'} and a.artifact_id.startswith('frame_')],
        'videos':[{ 'id':a.artifact_id,'v':a.version,'provider':a.provenance.provider_id}
            for a in artifacts if a.artifact_type.value=='video']}
    if compact:result.pop('references')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    main(compact='--compact' in sys.argv)
