"""Versioned correction of legacy resource failures misclassified as content review."""
from movie_agent.domain import HumanGateType,ReviewStatus,WorkflowNodeStatus,utc_now


def reclassify_resource_waits(engine):
    # Exact legacy normalized diagnostics, not substring guesses about visual content.
    diagnostics={'Spark control command failed','service is busy or draining','RESOURCE_EXHAUSTED',
        'provider concurrency limit','exclusive runtime lease'}
    store=engine.artifact_store;changed=[]
    records={}
    for artifact in store.list_all():
        if artifact.metadata.get('purpose')!='recovery_pending':continue
        evidence=store.read_structured(artifact)
        if evidence.get('reason') in diagnostics:records[evidence['node_id']]=(artifact,evidence)
    for review in engine.human_gates.all():
        if review.status!=ReviewStatus.PENDING or review.superseded_at is not None or review.gate_type!=HumanGateType.AGENT_ESCALATION:
            continue
        match=records.get(review.node_id)
        if not match or review.question!=match[1]['reason']:continue
        artifact,evidence=match
        engine.commit_quality('runtime_reclassification_'+review.review_id,{
            'policy_revision':'resource-wait-is-not-content-failure-1','original_review':review.model_dump(mode='json'),
            'original_evidence':{'artifact_id':artifact.artifact_id,'version':artifact.version,'sha256':artifact.metadata['sha256']},
            'classification':'system_resource_wait','human_approval_asserted':False,
            'resume_policy':'Existing exact outputs reused; uncertain leases must reconcile before dispatch; budgets unchanged.'},
            'runtime_failure_reclassification')
        engine.human_gates._reviews[review.review_id]=review.model_copy(update={'superseded_at':utc_now()})
        engine.current_production.set_status(review.node_id,WorkflowNodeStatus.PENDING,progress=0)
        changed.append(review.node_id)
    if changed:engine._durable_media_checkpoint()
    return changed
