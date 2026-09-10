"""Data-driven recovery DAG; project-specific material lives in caller-supplied plans."""
from movie_agent.domain import (ArtifactType, GenerationJob, HumanGateType, ResourceClass,
    WorkflowEdge, WorkflowGraph, WorkflowNode, WorkflowNodeStatus as S)
from movie_agent.media import (ImagePurpose, ReferenceType as R, VisionInspectionRequest,
    VisionInspectionProfile as VP, VisionDecision as D, Timeline, TimelineClip, VideoTrack)
from movie_agent.quality.reference_policy import REVISION, CRITIC_REVISION, AUTHORIZATION, scoped_reference_requirements
from movie_agent.quality.references import IdentityReference, ReferenceRole, hard_reference_subjects
from movie_agent.quality.recovery import RecoveryPlan
from movie_agent.quality.budget import RepairCostPolicy, BudgetExhausted
from movie_agent.quality.reports import ShotQualityStatus, QualityThresholds
from movie_agent.quality.cinematic import evaluate_cinematic
from movie_agent.media.reference_conditioning import conditioning_transport
from movie_agent.services.hero_production import HeroMovieProduction
from movie_agent.model_services.coordinator import ResourceAdmissionWait
from movie_agent.quality.budget import fingerprint
import asyncio

SHOT_POLICY = 'p5r-shot-usability-1'


class ReferenceRecoveryProduction(HeroMovieProduction):
    reference_policy_revision=REVISION
    shot_policy_revision=SHOT_POLICY
    critic_revision=CRITIC_REVISION
    policy_authorization=AUTHORIZATION
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.media_runtime.inspection_context={'quality_policy_revision':self.shot_policy_revision,
            'critic_config_revision':self.critic_revision,'recovery_authorization':self.policy_authorization}

    def recovery_plan(self):
        artifact=self.artifact_store.get('recovery_plan')
        if artifact is None:raise ValueError('An explicit recovery plan is required')
        return RecoveryPlan.model_validate(self.artifact_store.read_structured(artifact))

    def reclassify_resource_waits(self):
        from movie_agent.services.runtime_recovery import reclassify_resource_waits
        return reclassify_resource_waits(self)

    def _restore_extra(self,state):
        super()._restore_extra(state)
        saved=self.artifact_store.get('quality_recovery_authorization') or self.artifact_store.get('p5r_recovery_authorization')
        if saved:
            authorization=self.artifact_store.read_structured(saved)
            self.critic_revision=authorization['critic_config_revision']
            self.media_runtime.inspection_context={'quality_policy_revision':self.shot_policy_revision,
                'critic_config_revision':self.critic_revision,'recovery_authorization':self.policy_authorization}
        policy=self.artifact_store.get('active_quality_policy')
        if policy is not None:
            self.thresholds=QualityThresholds.model_validate(self.artifact_store.read_structured(policy))

    def configure_recovery(self,plan):
        self.commit_quality('recovery_plan',plan.model_dump(mode='json'),'recovery_plan')
        self.thresholds=QualityThresholds.for_profile(plan.quality_profile).model_copy(update={
            'minimum_accepted_seconds':plan.candidate.minimum_seconds,'minimum_accepted_shots':plan.candidate.minimum_shots,
            'target_seconds':self.current_project.brief.target_duration})
        self.commit_quality('active_quality_policy',self.thresholds.model_dump(mode='json'),'quality_policy')
        for shot in self.current_project.shots:shot.quality_profile=plan.quality_profile
        self._durable_media_checkpoint()

    def install_recovery(self):
        plan=self.recovery_plan();project=self.current_project;graph=self.current_production;ns=plan.namespace
        self.commit_quality('recovery_graph_history',graph.graph.model_dump(mode='json'),'recovery_graph_history')
        def add(identity,label,deps,order):
            if identity not in {n.node_id for n in graph.graph.nodes}:
                graph.add_node(WorkflowNode(node_id=identity,node_type='recovery',label=label,group='quality',role='Core',display_order=order))
                for dep in deps:graph.add_edge(WorkflowEdge(source_node_id=dep,target_node_id=identity))
        for action in plan.reference_actions:add(action.node_id,'Reference: '+action.subject_id,[plan.entry_node_id],action.priority)
        replacements={}
        for rank,shot in enumerate(project.shots):
            scene=next(s for s in project.scenes if s.scene_id==shot.scene_id)
            deps=[plan.reference_gate_nodes[subject] for _,subject in hard_reference_subjects(shot,scene)]
            priority=plan.shot_priorities.get(shot.shot_id,rank*10)
            for stage,parents,offset in [('frames',deps,3),('frame_gate',[ns+'_frames:'+shot.shot_id],2),
                ('video',[ns+'_frame_gate:'+shot.shot_id],1),('critic',[ns+'_video:'+shot.shot_id],0)]:
                identity=ns+'_'+stage+':'+shot.shot_id
                add(identity,stage+': '+shot.shot_id,parents,priority+offset-100)
                if stage=='frames' and graph.node(identity).status==S.PENDING:replacements[identity]=parents
        candidate=ns+'_candidate';add(candidate,'Assemble accepted candidate',[plan.entry_node_id],-1000)
        if candidate not in graph.node('audio_post').dependencies:graph.add_edge(WorkflowEdge(source_node_id=candidate,target_node_id='audio_post'))
        # Replanning changes only pending work; previous DAG snapshots and completed nodes survive.
        nodes=[n.model_copy(update={'dependencies':replacements[n.node_id]}) if n.node_id in replacements else n for n in graph.graph.nodes]
        edges=[e for e in graph.graph.edges if e.target_node_id not in replacements]
        edges.extend(WorkflowEdge(source_node_id=d,target_node_id=n) for n,deps in replacements.items() for d in deps)
        graph.graph=WorkflowGraph.model_validate(graph.graph.model_copy(update={'nodes':nodes,'edges':edges}).model_dump())
        self._durable_media_checkpoint()

    def candidate_ready(self,project):
        artifact=self.artifact_store.get('film_quality_report')
        if artifact is None:return False
        policy=self.recovery_plan().candidate;report=self.artifact_store.read_structured(artifact)
        accepted=[s for s in report['shots'] if s['status']=='accepted' and not s['blocking_issue_ids']]
        return (sum(s['duration_seconds'] for s in accepted)>=policy.minimum_seconds and len(accepted)>=policy.minimum_shots
            and set(policy.required_scene_ids)<={s['scene_id'] for s in accepted}
            and set(policy.required_dialogue_shot_ids)<={s['shot_id'] for s in accepted})

    def node_eligible(self,node,project,production):
        plan=self.recovery_plan()
        if node.node_id==plan.namespace+'_candidate':return self.candidate_ready(project)
        if node.node_id.startswith(plan.namespace+'_') and plan.candidate.export_before_optional_work and self.candidate_ready(project):return False
        return True

    async def recover_reference(self,project,action):
        selected=[r for r in self.pack().references if r.subject_id==action.subject_id and r.selected]
        # A human-accepted bundle remains authoritative. Downstream checks assess new outputs, not this baseline.
        if selected and (any(r.human_review_id for r in selected) or action.strategy=='reuse_selected'):return True
        if action.strategy=='reuse_selected':raise ValueError('Required selected reference unavailable')
        artifact=(await self.image(project,action.artifact_id,action.instruction,purpose=action.purpose,repair=True)
            if action.strategy=='generate' else self.artifact_store.get(action.artifact_id))
        if artifact is None:raise ValueError('Reference artifact unavailable')
        requirements=scoped_reference_requirements(action.reference_type,action.expected_description or action.instruction)
        requirements.extend(action.requirements)
        inspection=await self.inspect_frame(project,artifact,reference_role=action.reference_type,requirements=requirements)
        # Only new, proven material defects may trigger a finite structural repair.
        blockers=[i for i in inspection.issues if i.severity.value in {'major','critical'}]
        ledger=self.media_runtime.quality_ledger
        failures=[o for o in ledger.critic_outcomes() if o['subject']==artifact.artifact_id and o['quality_policy_revision']==self.reference_policy_revision
            and o['outcome']=='material_rejected']
        if inspection.decision!=D.PASS and blockers and len(failures)<ledger.budget.material_failures_per_policy:
            instruction='Create a corrected complete reference. Required subject: '+action.instruction+'\nFix observed defects: '+'; '.join(i.message for i in blockers)
            self.commit_quality('reference_repair_'+artifact.artifact_id,{'source':self.pin(artifact).model_dump(mode='json'),
                'inspection_result_id':inspection.result_id,'route':'frame_regenerate','maximum_automatic_repairs':1},'reference_repair_strategy')
            artifact=await self.image(project,artifact.artifact_id,instruction,purpose=action.purpose,repair=True)
            inspection=await self.inspect_frame(project,artifact,reference_role=action.reference_type,requirements=requirements)
        return self.save_reference(action.subject_id,action.reference_type,artifact,inspection,roles=action.roles)

    async def prepare_frames(self,project,shot):
        from movie_agent.media.compilers import GenericImagePromptCompiler
        refs=self.shot_references(project,shot)
        scene=next(s for s in project.scenes if s.scene_id==shot.scene_id)
        location=next(l for l in project.locations if l.location_id==scene.location_id)
        prompt=GenericImagePromptCompiler().compile(shot,ImagePurpose.FIRST_FRAME,refs).positive_prompt
        prompt+='\nSTART boundary pose only. Location: '+location.description
        prompt+='\nUse each identity/body/wardrobe reference only for its stated entity and semantic role. Single coherent shot, no text.'
        capabilities=await self.media_runtime.capabilities()
        revised=bool(self.artifact_store.get('reference_invalidation_'+shot.shot_id))
        transport=conditioning_transport(refs,capabilities)
        if len(refs)>1:
            if not transport:raise ValueError('No provider supports the required multi-reference conditioning transport')
            first=await self.image(project,'frame_'+shot.shot_id+'_first',prompt,shot=shot,references=refs,
                conditioning=transport,repair=revised)
        else:
            first=await self.image(project,'frame_'+shot.shot_id+'_first',prompt,shot=shot,
                source=refs[0].model_copy(update={'reference_type':R.SOURCE_IMAGE}),repair=revised)
        last_prompt='Preserve source identities, wardrobe and setting. Show only the END boundary pose of this same shot. '
        last_prompt+=shot.narrative.action_summary+'\nEND STATE: '+shot.expected_state_after.model_dump_json()
        last=await self.image(project,'frame_'+shot.shot_id+'_last',last_prompt,source=self.pin(first),purpose=ImagePurpose.LAST_FRAME,shot=shot,repair=revised)
        shot.frame_anchors.first_frame.source_artifact_id=first.artifact_id;shot.frame_anchors.last_frame.source_artifact_id=last.artifact_id
        from movie_agent.domain import AnchorKind
        shot.frame_anchors.first_frame.kind=AnchorKind.GENERATED
        shot.frame_anchors.last_frame.kind=AnchorKind.GENERATED
        self.commit_quality('p5r_frame_pair_'+shot.shot_id,{'first':self.pin(first).model_dump(mode='json'),
            'last':self.pin(last).model_dump(mode='json')},'recovery_frame_pair')

    async def critique_shot(self,project,shot):
        video=self.artifact_store.list_versions('video_'+shot.shot_id)[-1]
        self._record_evaluation(project,self.technical_qc.evaluate(shot,video))
        self._record_evaluation(project,await self._vision_evaluation(project,shot,video))
        self._record_evaluation(project,await evaluate_cinematic(self,project,shot=shot,artifact=video,minimum=self.thresholds.cinematic_minimum))
        await self._repair_accept(project,shots=[shot])
        return any(r.shot_id==shot.shot_id and r.status==ShotQualityStatus.ACCEPTED for r in self.reports(project))

    async def _execute_node(self,node_id,project,production,*,auto_approve):
        plan=self.recovery_plan();self.active_recovery_node=node_id
        action=next((a for a in plan.reference_actions if a.node_id==node_id),None)
        if node_id==plan.namespace+'_candidate':self.assemble_candidate(project);return True
        if not action and not node_id.startswith(plan.namespace+'_'):
            return await super()._execute_node(node_id,project,production,auto_approve=False)
        subject=action.subject_id if action else node_id.split(':',1)[-1]
        shot=next((s for s in project.shots if s.shot_id==subject),None)
        try:
            failure_class='content_quality'
            passed=True
            if action:passed=await self.recover_reference(project,action)
            elif node_id==plan.namespace+'_frames:'+subject:await self.prepare_frames(project,shot)
            elif node_id==plan.namespace+'_frame_gate:'+subject:passed=await self.gate_frames(project,shot)
            elif node_id==plan.namespace+'_video:'+subject:
                await self._generate_versions(project,[shot])
            elif node_id==plan.namespace+'_critic:'+subject:passed=await self.critique_shot(project,shot)
            else:raise ValueError('Unknown recovery node; explicit plan required')
            if passed:return True
            reason='Core usability gate did not pass; independent ready work continues.'
        except ResourceAdmissionWait as error:
            identity='runtime_admission_'+fingerprint(node_id)[:24]
            attempt=len(self.artifact_store.list_versions(identity))+1
            self.commit_quality(identity,{'node_id':node_id,'attempt':attempt,'reason':str(error),
                'classification':'system_resource_wait','inference_dispatched':False,
                'disposition':'retry_runtime' if attempt<plan.resource_admission_attempts else 'pause_runtime'},'runtime_admission_wait')
            if attempt<plan.resource_admission_attempts:
                await asyncio.sleep(plan.resource_retry_delay_seconds)
                return await self._execute_node(node_id,project,production,auto_approve=False)
            production.set_status(node_id,S.PENDING,progress=0)
            return False
        except Exception as error:
            reason=str(error);failure_class='system_execution'
        if shot and failure_class=='content_quality':self.block_shot(shot,'recovery',reason)
        self.commit_quality('recovery_pending_'+subject,{'node_id':node_id,'reason':reason,
            'classification':failure_class,
            'disposition':'omit_from_candidate' if shot and failure_class=='content_quality' else 'human_review',
            'human_approved':False},'recovery_pending')
        if not self.human_gates.pending_for_node(node_id):self.human_gates.request(project.project_id,node_id,
            HumanGateType.AGENT_ESCALATION,reason,['reference_identity_set' if action else 'human_review_'+subject])
        production.set_status(node_id,S.WAITING_HUMAN,progress=0)
        return False
    async def inspect_frame(self,project,artifact,*,shot=None,refs=None,requirements=None,reference_role=None):
            identity='frame_inspection_'+artifact.artifact_id+'_v'+str(artifact.version)
            profiles=[VP.IMAGE_QUALITY,VP.ARTIFACT_DETECTION,VP.PROMPT_ALIGNMENT]
            if any(r.reference_type==R.CHARACTER for r in refs or []):profiles.append(VP.CHARACTER_IDENTITY)
            if any(r.reference_type==R.LOCATION for r in refs or []):profiles.append(VP.SCENE_CONSISTENCY)
            ledger=self.media_runtime.quality_ledger
            revision=self.reference_policy_revision if reference_role else self.shot_policy_revision
            # Only definitely completed invalid proposals are eligible for another bounded critic revision.
            outcomes=ledger.critic_outcomes()
            invalid=[o for o in outcomes if o['subject']==artifact.artifact_id and o['quality_policy_revision']==revision
                     and o['critic_config_revision']==self.critic_revision
                     and o['version']==artifact.version and o['outcome']=='critic_invalid_or_transport']
            first_revision=len(invalid)+1
            for attempt in range(first_revision,4):
                request=VisionInspectionRequest(job_id=identity,project_id=project.project_id,
                    shot_id=shot.shot_id if shot else None,scene_id=shot.scene_id if shot else None,
                    image_artifact_id=artifact.artifact_id,target_artifact_version=artifact.version,target_sha256=self.pin(artifact).sha256,
                    reference_assets=refs or [],expected_shot=shot,expected_requirements=requirements or [],
                    profiles=profiles,output_artifact_id=identity,output_language=project.brief.output_language,retry_budget=1,
                    inspection_revision=attempt,reference_role=reference_role,quality_policy_revision=revision,
                    quality_profile=shot.quality_profile if shot else self.thresholds.profile,
                    critic_config_revision=self.critic_revision,recovery_authorization=self.policy_authorization)
                try:
                    return await self.media_runtime.inspect(GenerationJob(job_id=identity,project_id=project.project_id,
                        node_id=getattr(self,'active_recovery_node','reference_gate'),
                        task='vision',resource_class=ResourceClass.LIGHT,idempotency_key=identity),request)
                except Exception as error:
                    completed=getattr(error,'attempts',[]) or (ledger.critic_outcomes()[-1].get('provider_attempts',[]) if ledger.critic_outcomes() else [])
                    if isinstance(error,BudgetExhausted) or not completed or any(a.get('outcome')=='uncertain' for a in completed):raise
                    if attempt==3:raise
            raise BudgetExhausted('No critic retry remains for this exact image')

    def save_reference(self,subject,kind,artifact,inspection,*,roles=None):
            pack=self.pack();prior=[r for r in pack.references if r.subject_id==subject]
            roles=roles or list(dict.fromkeys(r.reference_role for r in prior)) or [{R.CHARACTER:ReferenceRole.PORTRAIT,
                R.LOCATION:ReferenceRole.ENVIRONMENT,R.PROP:ReferenceRole.PROP,R.STYLE:ReferenceRole.STYLE}[kind]]
            pack.references=[r for r in pack.references if r.subject_id!=subject]
            pin=self.pin(artifact)
            pack.references.extend(IdentityReference(artifact_id=pin.artifact_id,version=pin.version,sha256=pin.sha256,
                reference_type=kind,reference_role=role,subject_id=subject,selected=inspection.decision==D.PASS,
                quality_status=inspection.decision,inspection_result_id=inspection.result_id,
                score=min(s.score for s in inspection.scores)) for role in roles)
            self.commit_quality('reference_identity_set',pack.model_dump(mode='json'),'reference_identity_set')
            return inspection.decision==D.PASS

    async def gate_frames(self,project,shot):
            refs=self.shot_references(project,shot);pair=self.artifact_store.read_structured(self.artifact_store.get('p5r_frame_pair_'+shot.shot_id))
            results=[];frames=[]
            for end in ('first','last'):
                original=pair[end];artifact=self.artifact_store.get(original['artifact_id'],original['version'])
                repair_record=self.artifact_store.get('p5r_frame_repair_'+shot.shot_id+'_'+end)
                successors=[a for a in self.artifact_store.list_versions(artifact.artifact_id) if a.version>artifact.version]
                repair_used=bool(repair_record and successors)
                if repair_used:artifact=successors[-1]
                # A boundary frame must not be judged as a finished action or a fully lit reference plate.
                expected=shot.model_copy(deep=True)
                if end=='last':expected.state_before=shot.expected_state_after.model_copy(deep=True)
                requirements=[f'This is the {end.upper()} boundary still for this shot. Compare visible canonical pose, '
                    'hard identity/location/visible props to the exact supplied references. Minor lighting/style/composition '
                    'differences are warnings. Do not require motion or completed action from a still. '
                    'Do not demand objects/characters or body parts outside this shot framing. In a hand insert, judge visible skin, sleeves and props; an offscreen face is not a missing person. '
                    'Major/critical require concrete observable subject, mismatch, role-specific blocking reason and core_usability_affected=true.']
                result=await self.inspect_frame(project,artifact,shot=expected,refs=refs,requirements=requirements)
                blockers=[i for i in result.issues if i.severity.value in {'major','critical'}]
                if result.decision!=D.PASS and blockers and not repair_used:
                    from movie_agent.media import RepairContext
                    context=RepairContext(target_artifact_id=artifact.artifact_id,target_artifact_version=artifact.version,
                        target_sha256=self.pin(artifact).sha256,inspection_result_id=result.result_id,
                        preserve=['Preserve every accepted canonical reference identity and semantic role.'],
                        fix=[i.message for i in blockers])
                    refs=self.shot_references(project,shot,repair_context=context)
                    structural=any(i.issue_type.value not in {'lighting_mismatch','style_mismatch'} for i in blockers)
                    capabilities=await self.media_runtime.capabilities()
                    repair_refs=[self.pin(artifact).model_copy(update={'semantic_role':'repair_base'}),*refs]
                    transport=conditioning_transport(repair_refs,capabilities)
                    if not transport:
                        repair_refs=refs;transport=conditioning_transport(refs,capabilities)
                    estimate=RepairCostPolicy().route_image(structural=structural,
                        preserve_reference_identity=bool(refs),reference_conditioning_available=bool(transport))
                    if estimate is None:
                        results.append(result);frames.append(artifact);break
                    reason='; '.join(i.message for i in blockers)
                    instruction='Preserve the accepted identities and wardrobe from their semantic reference panels. '
                    instruction+='Use the repair_base panel for composition only; correct its listed defects. '
                    instruction+='Fix only these observed defects: '+reason+'\nCanonical shot: '+expected.model_dump_json()
                    self.commit_quality('p5r_frame_repair_'+shot.shot_id+'_'+end,{'reason':reason,
                        'chosen':estimate.model_dump(mode='json'),'source':self.pin(artifact).model_dump(mode='json'),
                        'conditioning':transport,'references':[r.model_dump(mode='json') for r in repair_refs],
                        'policy':'preserve_accepted_reference_identity'},'targeted_frame_repair')
                    artifact=await self.image(project,artifact.artifact_id,instruction,
                        source=None if refs or structural else self.pin(artifact),references=repair_refs if refs else None,
                        conditioning=transport if refs else None,purpose=ImagePurpose.FIRST_FRAME if end=='first' else ImagePurpose.LAST_FRAME,
                        shot=shot,repair=True)
                    result=await self.inspect_frame(project,artifact,shot=expected,refs=refs,requirements=requirements)
                results.append(result);frames.append(artifact)
                if result.decision!=D.PASS:break  # A failed boundary already makes this shot ineligible.
            passed=len(results)==2 and all(r.decision==D.PASS for r in results)
            from movie_agent.media.video_conditioning import reference_fingerprint
            self.commit_quality('frame_gate_'+shot.shot_id,{'passed':passed,'shot_id':shot.shot_id,
                'inspections':[r.result_id for r in results],'frames':[self.pin(f).model_dump(mode='json') for f in frames],
                'reference_fingerprint':reference_fingerprint(refs),
                'reference_assets':[r.model_dump(mode='json') for r in refs]},'frame_gate')
            if passed:
                for artifact in frames:self._select(project,artifact.artifact_id,artifact.version)
                self.commit_quality('frame_bindings_'+shot.shot_id,[self.pin(frames[0],R.FIRST_FRAME).model_dump(mode='json'),
                    self.pin(frames[1],R.LAST_FRAME).model_dump(mode='json')],'exact_frame_bindings')
            return passed

    def assemble_candidate(self,project):
            reports=self.reports(project);accepted=[r for r in reports if r.status==ShotQualityStatus.ACCEPTED]
            if not self.candidate_ready(project):raise ValueError('Candidate minimum and story coverage are not met')
            offset=0;clips=[]
            for report in accepted:
                clips.append(TimelineClip(clip_id='p5r_'+report.shot_id,shot_id=report.shot_id,
                    artifact_id=report.artifact_id,version=report.artifact_version,start_time_seconds=offset,
                    duration_seconds=report.duration_seconds))
                offset+=report.duration_seconds
            self.timeline=Timeline(project_id=project.project_id,duration_seconds=offset,video_tracks=[VideoTrack(clips=clips)])
            self._durable_media_checkpoint()
