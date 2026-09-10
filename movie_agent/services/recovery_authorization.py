"""Versioned quality authorizations and explicit human acceptance of semantic reference bundles."""
from movie_agent.domain import HumanGateType, ReviewStatus, WorkflowNode, WorkflowEdge, WorkflowNodeStatus, utc_now
from movie_agent.quality.recovery import HumanReferenceAcceptance
from movie_agent.quality.references import IdentityReference
from movie_agent.media import VisionDecision


def configure_quality_authorization(engine, authorization, *, user_statement):
    if not user_statement.strip():raise ValueError('An explicit authorization statement is required')
    if not authorization.get('policy_revisions') or not authorization.get('critic_config_revision'):
        raise ValueError('Quality authorization must identify policy and critic configuration')
    if any(not isinstance(v,int) or not 1<=v<=3 for v in authorization.get('material_failure_overrides',{}).values()):
        raise ValueError('Material budget amendment exceeds the finite limit')
    engine.commit_quality('quality_recovery_authorization',{**authorization,'user_statement':user_statement},'recovery_authorization')
    engine.critic_revision=authorization['critic_config_revision']
    if hasattr(engine.media_runtime,'inspection_context'):
        engine.media_runtime.inspection_context={**engine.media_runtime.inspection_context,
            'critic_config_revision':engine.critic_revision}
    if authorization.get('supersede_invalid_critic_reviews'):
        supersede_invalid_critic_reviews(engine,authorization)


def supersede_invalid_critic_reviews(engine,authorization):
    """Requeue completed invalid technical proposals after an authorized critic upgrade.

    Material reviews, uncertain dispatches, human acceptance and final approval are
    never candidates. All reservations and negative proposals remain immutable.
    """
    store=engine.artifact_store
    if not store.get('recovery_plan'):return []
    plan=engine.recovery_plan();ledger=engine.media_runtime.quality_ledger
    outcomes=ledger.critic_outcomes();changed=[]
    for review in engine.human_gates.all():
        if (review.status!=ReviewStatus.PENDING or review.superseded_at is not None
            or review.gate_type!=HumanGateType.AGENT_ESCALATION):continue
        if review.question not in {'VLM structured/semantic output invalid after bounded repair',
                                  'No critic retry remains for this exact image','GENERATION_FAILED'}:continue
        sources=[]
        action=next((a for a in plan.reference_actions if a.node_id==review.node_id),None)
        if action:
            artifact=store.get(action.artifact_id)
            if artifact:sources=[engine.pin(artifact)]
        prefix=plan.namespace+'_frame_gate:'
        if review.node_id.startswith(prefix):
            shot_id=review.node_id[len(prefix):]
            pair=store.get('p5r_frame_pair_'+shot_id)
            if pair:
                data=store.read_structured(pair)
                for end in ('first','last'):
                    selected=data[end]
                    artifact=store.get(selected['artifact_id'])
                    if artifact:sources.append(engine.pin(artifact))
        evidence=[]
        for source in sources:
            matches=[o for o in outcomes if o['subject']==source.artifact_id
                and o['version']==source.version and o['sha256']==source.sha256]
            if not matches:continue
            last=matches[-1];attempts=last.get('provider_attempts',[])
            if (last['outcome']=='critic_invalid_or_transport'
                and last['critic_config_revision']!=authorization['critic_config_revision']
                and last['quality_policy_revision'] in authorization['policy_revisions']
                and attempts and all(a.get('finish_reason')=='stop' and a.get('rejected_proposal') for a in attempts)):
                evidence.append(last)
        if not evidence:continue
        engine.commit_quality('critic_supersession_'+review.review_id,{
            'original_review':review.model_dump(mode='json'),'original_outcomes':evidence,
            'critic_config_revision':authorization['critic_config_revision'],
            'classification':'completed_invalid_critic_proposal','human_approval_asserted':False,
            'budgets_reset':False},'critic_technical_supersession')
        engine.human_gates._reviews[review.review_id]=review.model_copy(update={'superseded_at':utc_now()})
        engine.current_production.set_status(review.node_id,WorkflowNodeStatus.PENDING,progress=0)
        changed.append(review.node_id)
    if changed:engine._durable_media_checkpoint()
    return changed


def accept_reference_bundle(engine, command: HumanReferenceAcceptance):
    from movie_agent.quality.budget import fingerprint
    store=engine.artifact_store;graph=engine.current_production;project=engine.current_project
    applied='applied_'+command.acceptance_id
    canonical=command.model_dump(mode='json',exclude={'superseded_review_ids'})
    for binding in canonical['bindings']:binding['reference'].pop('reference_id',None)
    key=fingerprint(canonical)
    existing_acceptance=store.get(command.acceptance_id)
    if existing_acceptance and store.read_structured(existing_acceptance).get('command_fingerprint')!=key:
        raise ValueError('An acceptance identity cannot be reused for different references or limitations')
    if store.get(applied):
        if store.read_structured(store.get(command.acceptance_id)).get('command_fingerprint')!=key:
            raise ValueError('An acceptance identity cannot be reused for different references or limitations')
        return
    for binding in command.bindings:
        artifact=store.get(binding.reference.artifact_id,binding.reference.version)
        if artifact is None or engine.pin(artifact).sha256!=binding.reference.sha256:
            raise ValueError('Human acceptance does not match the exact artifact shown to the user')
    plan=engine.recovery_plan();node=command.workflow_node_id
    existing_node=next((n for n in graph.graph.nodes if n.node_id==node),None)
    if existing_node and existing_node.node_type!='human_reference_acceptance':
        raise ValueError('Reference acceptance cannot replace another workflow gate')
    reviews=[r for r in engine.human_gates.all() if r.node_id==node]
    if any(r.context_artifact_ids!=[command.acceptance_id] or r.question!=command.question for r in reviews):
        raise ValueError('Reference acceptance cannot reuse a review of another bundle')
    subjects={b.subject_id for b in command.bindings}
    allowed_nodes={a.node_id for a in plan.reference_actions if a.subject_id in subjects}
    for identity in command.superseded_review_ids:
        old=engine.human_gates.get(identity)
        if old.node_id not in allowed_nodes or old.gate_type!=HumanGateType.AGENT_ESCALATION:
            raise ValueError('Supersession is outside this accepted reference bundle')
    if not any(n.node_id==node for n in graph.graph.nodes):
        graph.add_node(WorkflowNode(node_id=node,node_type='human_reference_acceptance',label='Human accepted reference bundle',
            role='Human',group='quality',display_order=18))
        graph.add_edge(WorkflowEdge(source_node_id=plan.entry_node_id,target_node_id=node))
    review=next((r for r in engine.human_gates.all() if r.node_id==node),None)
    if review is None:
        review=engine.human_gates.request(project.project_id,node,HumanGateType.AGENT_ESCALATION,
            command.question,[command.acceptance_id])
    if review.status==ReviewStatus.PENDING:
        review=engine.human_gates.resolve(review.review_id,True,command.user_statement)
    if review.status!=ReviewStatus.APPROVED:raise ValueError('Reference bundle was not approved')
    old_reviews=[]
    for identity in command.superseded_review_ids:
        old=engine.human_gates.get(identity)
        if old.node_id not in allowed_nodes or old.gate_type!=HumanGateType.AGENT_ESCALATION:
            raise ValueError('Supersession is outside this accepted reference bundle')
        if old.status==ReviewStatus.PENDING and old.superseded_at is None:
            old_reviews.append(old.model_dump(mode='json'))
            engine.human_gates._reviews[old.review_id]=old.model_copy(update={'superseded_at':utc_now()})
    payload={'human_review':review.model_dump(mode='json'),'bindings':[b.model_dump(mode='json') for b in command.bindings],
        'command_fingerprint':key,
        'selection_basis':'human accepted with known limitations','prior_reviews':old_reviews,
        'original_vlm_results_unchanged':True,'final_film_aesthetic_approval':False}
    engine.commit_quality(command.acceptance_id,payload,'human_reference_acceptance')
    accepted_roles={(b.subject_id,b.role) for b in command.bindings}
    pack=engine.pack();pack.references=[r for r in pack.references if (r.subject_id,r.reference_role) not in accepted_roles]
    for binding in command.bindings:
        ref=binding.reference
        pack.references.append(IdentityReference(artifact_id=ref.artifact_id,version=ref.version,sha256=ref.sha256,
            reference_type=ref.reference_type,reference_role=binding.role,subject_id=binding.subject_id,selected=True,
            quality_status=VisionDecision.HUMAN_REVIEW,human_review_id=review.review_id,
            acceptance_artifact_id=command.acceptance_id,known_limitations=binding.known_limitations))
    engine.commit_quality('reference_identity_set',pack.model_dump(mode='json'),'reference_identity_set')
    plan.reference_gate_nodes.update({subject:node for subject in subjects})
    engine.configure_recovery(plan)
    graph.set_status(node,WorkflowNodeStatus.SUCCEEDED)
    engine.commit_quality(applied,{'acceptance_id':command.acceptance_id,'review_id':review.review_id},'reference_acceptance_applied')
    engine._durable_media_checkpoint()
