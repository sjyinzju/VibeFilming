"""Formal production engine: canonical planning expands into independent quality-gated work."""
from movie_agent.domain import HumanGateType, WorkflowNodeStatus as S
from movie_agent.services.reference_recovery import ReferenceRecoveryProduction
from movie_agent.services.mock_production import MockMovieProduction
from movie_agent.quality.production import FilmProductionPolicy, plan_references
from movie_agent.quality.reports import QualityThresholds
from movie_agent.quality.budget import QualityBudget
from movie_agent.services.recovery_authorization import configure_quality_authorization


class FilmProduction(ReferenceRecoveryProduction):
    reference_policy_revision='reference-usability-1'
    shot_policy_revision='shot-usability-1'
    critic_revision='observable-core-evidence-3'
    policy_authorization='project-production-policy-1'
    aggregate_stages={'storyboard_planning':'frame_gate','shot_production':'video',
        'technical_qc':'critic','visual_semantic_critic':'critic','cinematic_critic':'critic','repair_accept':'critic'}

    def initialize_project(self,project,graph,policy=None):
        self.current_project,self.current_production=project,graph
        policy=policy or FilmProductionPolicy()
        self.commit_quality('production_policy',policy.model_dump(mode='json'),'production_policy')
        self.thresholds=QualityThresholds.for_profile(project.brief.quality_level).model_copy(update={'target_seconds':project.brief.target_duration})
        self.commit_quality('active_quality_policy',self.thresholds.model_dump(mode='json'),'quality_policy')
        configure_quality_authorization(self,{'policy_revisions':[self.reference_policy_revision,self.shot_policy_revision],
            'critic_config_revision':self.critic_revision,'recovery_authorization':self.policy_authorization},
            user_statement='New project production under the persisted bounded production policy; no historical budget reset.')

    def production_policy(self):
        artifact=self.artifact_store.get('production_policy')
        return FilmProductionPolicy.model_validate(self.artifact_store.read_structured(artifact)) if artifact else FilmProductionPolicy()

    def _restore_extra(self,state):
        super()._restore_extra(state)
        artifact=self.artifact_store.get('active_compute_budget')
        if artifact:
            self.media_runtime.quality_ledger.budget=QualityBudget.model_validate(self.artifact_store.read_structured(artifact))

    def settled(self,identity,graph,seen=None):
        seen=set() if seen is None else seen
        if identity in seen:raise ValueError('Cycle in production dependencies')
        node=graph.node(identity)
        if node.status in {S.SUCCEEDED,S.WAITING_HUMAN,S.FAILED,S.CANCELLED}:return True
        return any(graph.node(parent).status!=S.SUCCEEDED and self.settled(parent,graph,seen|{identity})
            for parent in node.dependencies)

    def node_eligible(self,node,project,production):
        if self.artifact_store.get('recovery_plan') is None:return True
        plan=self.recovery_plan()
        if not plan.candidate.export_before_optional_work and (node.node_id==plan.namespace+'_candidate' or node.node_id in self.aggregate_stages):
            if not all(self.settled(plan.namespace+'_critic:'+s.shot_id,production) for s in project.shots):return False
        if node.node_id in self.aggregate_stages:
            stage=self.aggregate_stages[node.node_id];ns=self.recovery_plan().namespace
            return self.candidate_ready(project) or all(self.settled(ns+'_'+stage+':'+s.shot_id,production) for s in project.shots)
        return super().node_eligible(node,project,production)

    async def _execute_node(self,node_id,project,production,*,auto_approve):
        if node_id=='brief' and self.artifact_store.get('production_policy') is None:
            self.initialize_project(project,production)
        policy=self.production_policy()
        if node_id=='brief' and policy.require_real_providers:
            capabilities=await self.media_runtime.capabilities()
            actual=[c for c in capabilities if not c.provider_id.startswith('mock')]
            required={'image':any(c.image and c.image.text_to_image for c in actual),
                'reference_conditioning':any(c.image and (c.image.multi_reference or c.image.reference_sheet) for c in actual),
                'video':any(c.video and (c.video.first_frame or c.video.first_last_frame) for c in actual),
                'vision':any(c.vision and c.vision.image and c.vision.video for c in actual),
                'speech':any(c.audio and c.audio.tts for c in actual),'music':any(c.audio and c.audio.music for c in actual),
                'post':any('post' in [m.value for m in c.modalities] for c in actual)}
            self.commit_quality('production_readiness',required,'production_readiness')
            missing=[key for key,value in required.items() if not value]
            if missing:
                if not self.human_gates.pending_for_node(node_id):self.human_gates.request(project.project_id,node_id,
                    HumanGateType.AGENT_ESCALATION,'Configure real production capabilities: '+', '.join(missing),['production_readiness'])
                production.set_status(node_id,S.WAITING_HUMAN);return False
        if node_id in {'story_gate','shot_gate'}:
            if policy.review_planning:
                return await MockMovieProduction._execute_node(self,node_id,project,production,auto_approve=False)
            self.commit_quality('planning_policy_'+node_id,{'policy_revision':policy.revision,'human_approval_asserted':False,
                'decision':'continue_under_project_policy'},'planning_policy_decision')
            return True
        if node_id=='asset_planning':
            if self.artifact_store.get('active_compute_budget') is None:
                budget=policy.quality_budget or QualityBudget(h3_total=max(1,2*len(project.shots)))
                self.media_runtime.quality_ledger.budget=budget
                self.commit_quality('active_compute_budget',budget.model_dump(mode='json'),'compute_budget')
            self.configure_recovery(plan_references(project,policy));self.install_recovery();return True
        if node_id in self.aggregate_stages:
            stage=self.aggregate_stages[node_id];ns=self.recovery_plan().namespace
            self.commit_quality('stage_summary_'+node_id,{'execution':'independent_quality_dag','children':[
                {'shot_id':s.shot_id,'status':production.node(ns+'_'+stage+':'+s.shot_id).status.value} for s in project.shots]},'production_stage_summary')
            return True
        if self.artifact_store.get('recovery_plan') is None:
            return await MockMovieProduction._execute_node(self,node_id,project,production,auto_approve=False)
        return await super()._execute_node(node_id,project,production,auto_approve=False)
