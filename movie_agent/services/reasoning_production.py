"""Real reasoning stages composed with existing deterministic and mock media production."""

from movie_agent.domain import (
    Project, ArtifactType, CreativeDirection, StoryBible, VisualBible, Screenplay, ScreenplayScene,
    ScenePlan, ShotPlan, Scene, Shot, ShotNarrative, CameraSpec, ShotSize, LightingSpec,
    ContinuityChain, Provenance, EventType, GenerationJob, JobStatus, ResourceClass,
    GenerationHistoryEntry,
)
from movie_agent.orchestration.runtime.contracts import (
    RoleId, RoleInvocation, RoleResult, ContractSchemaRevision, RequestContractTypeRevision,
    SemanticContractRevision,
)
from movie_agent.orchestration.runtime.context import content_hash
from movie_agent.orchestration.runtime.cinematographer_drafts import (
    CinematographerDraftMapper, ShotPlanDraft,
)
from movie_agent.orchestration.runtime.runner import RoleRunner, RoleOutputInvalid
from movie_agent.orchestration.runtime.terminal import TerminalRepairDraft, is_terminal_only
from movie_agent.providers.base import LLMProvider
from .mock_production import MockMovieProduction


class ReasoningMovieProduction(MockMovieProduction):
    """Showrunner with selectable real reasoning roles and the Phase 1 media pipeline."""

    def __init__(self, workspace, llm_provider: LLMProvider, *, real_roles=None, event_bus=None):
        super().__init__(workspace, event_bus=event_bus)
        self.llm_provider = llm_provider
        self.real_roles = set(RoleId(r) for r in real_roles) if real_roles is not None else set(RoleId)
        self.role_results: dict[str, RoleResult] = {}
        self.role_revision_history: list[RoleResult] = []
        self.contract_schema_revisions: list[ContractSchemaRevision] = []
        self.request_contract_type_revisions: list[RequestContractTypeRevision] = []
        self.runner = RoleRunner(llm_provider, self.event_bus, self.trace_id)

    async def _role(self, role: RoleId, node: str, project: Project, *, scene: Scene | None = None):
        key = role.value + (":" + scene.scene_id if scene else "")
        saved = self.role_results.get(key)
        definition = self.runner.registry.get(role)
        target = self.runner.registry.committed_target(definition)
        if saved and saved.committed:
            return target.model_validate(saved.output)
        invocation = saved.invocation if saved else RoleInvocation(
            invocation_id=f"{project.project_id}:{key}:revision{sum(r.invocation.role_id == role for r in self.role_revision_history)}", role_id=role,
            project_id=project.project_id, node_id=node, scene_id=scene.scene_id if scene else None)
        restored_job = self._restored_jobs.get(invocation.invocation_id)
        if restored_job and restored_job.status == JobStatus.CANCELLED:
            raise RuntimeError("Cancelled role job requires explicit revision before resume")
        job = GenerationJob(job_id=invocation.invocation_id, project_id=project.project_id,
            node_id=node, task="structured_text", idempotency_key=invocation.invocation_id,
            resource_class=ResourceClass.LIGHT, retry_budget=definition.output_policy.repair_budget,
            retry_count=max(0, len(saved.attempts) - 1) if saved else 0,
            provenance=Provenance(role=role.value, tool="role_runner", provider_id=self.llm_provider.provider_id))
        self.job_manager.add(job)
        for status in (JobStatus.QUEUED, JobStatus.PREPARING, JobStatus.RUNNING):
            self.job_manager.transition(job.job_id, status)
        def persist_attempt(result):
            self.role_results[key] = result
            while self.job_manager.get(job.job_id).retry_count < len(result.attempts) - 1:
                self.job_manager.increment_retry(job.job_id)
            if self.current_production:
                self._save_checkpoint(project, self.current_production)
        try:
            result = await self.runner.run(invocation, project, scene=scene, previous=saved,
                                           on_attempt=persist_attempt)
        except RoleOutputInvalid as exc:
            self.role_results[key] = exc.result
            self.job_manager.transition(job.job_id, JobStatus.FAILED, failure_reason=exc.result.failure_code or "ROLE_OUTPUT_INVALID")
            raise
        except Exception as exc:
            failure = getattr(getattr(exc, "error_type", None), "value", "ROLE_PROVIDER_FAILED")
            self.job_manager.transition(job.job_id, JobStatus.FAILED, failure_reason=failure)
            project.generation_history.entries.append(GenerationHistoryEntry(job_id=job.job_id,
                strategy_type="structured_text", succeeded=False, provider_id=self.llm_provider.provider_id,
                issue_codes=[failure]))
            raise
        output = target.model_validate(result.output)
        if self.job_manager.get(job.job_id).cancellation_requested:
            self.job_manager.transition(job.job_id, JobStatus.CANCELLED)
            raise RuntimeError("Role job cancelled before state commit")
        # Commit only after both schema and semantic checks succeed.
        self._commit(project, output)
        result.committed = True
        self.role_results[key] = result
        artifact = self.artifact_store.create_placeholder(ArtifactType.TEXT, result.output,
            artifact_id="role_" + key.replace(":", "_"), source_job_id=job.job_id,
            provenance=Provenance(role=role.value, tool="role_runner", provider_id=result.provider_id,
                input_artifact_ids=result.context.source_ids,
                parameters={"role_result": result.model_dump(mode="json")},
                retry_history=[a.request_id for a in result.attempts[1:]]))
        self.job_manager.get(job.job_id).related_artifact_ids = [artifact.artifact_id]
        self.job_manager.get(job.job_id).provenance = artifact.provenance.model_copy(deep=True)
        self._emit(EventType.ARTIFACT_CREATED, project.project_id,
                   {"artifact_id": artifact.artifact_id, "version": artifact.version}, node_id=node, job_id=job.job_id)
        while self.job_manager.get(job.job_id).retry_count < len(result.attempts) - 1:
            self.job_manager.increment_retry(job.job_id)
        self.job_manager.transition(job.job_id, JobStatus.SUCCEEDED)
        project.generation_history.entries.append(GenerationHistoryEntry(job_id=job.job_id,
            strategy_type="structured_text", succeeded=True, provider_id=result.provider_id,
            metadata={"role_id": role.value, "context_hash": result.context.context_hash}))
        if self.current_production:
            graph_node = self.current_production.node(node)
            graph_node.output_refs = list(dict.fromkeys([*graph_node.output_refs, artifact.artifact_id]))
            self._save_checkpoint(project, self.current_production)
        return output

    @staticmethod
    def _commit(project: Project, output):
        if isinstance(output, CreativeDirection):
            project.creative_direction = output
        elif isinstance(output, StoryBible):
            project.story_bible = output
        elif isinstance(output, VisualBible):
            project.visual_bible = output
        elif isinstance(output, Screenplay):
            project.screenplay = output
            project.characters, project.locations, project.props = output.characters, output.locations, output.props
        elif isinstance(output, ScenePlan):
            project.scenes = output.scenes
        elif isinstance(output, ShotPlan):
            old_chain_ids = {s.continuity_chain_id for s in project.shots if s.scene_id == output.scene_id}
            project.shots = [s for s in project.shots if s.scene_id != output.scene_id] + output.shots
            project.continuity_chains = [c for c in project.continuity_chains if c.chain_id not in old_chain_ids] + output.continuity_chains
            for scene in project.scenes:
                if scene.scene_id == output.scene_id:
                    scene.shot_ids = [s.shot_id for s in output.shots]

    async def _creative_expansion(self, project):
        if RoleId.CREATIVE_PRODUCER in self.real_roles:
            await self._role(RoleId.CREATIVE_PRODUCER, "creative_expansion", project)
        else:
            await super()._creative_expansion(project)

    async def _story_planning(self, project):
        if RoleId.STORY_ARCHITECT in self.real_roles:
            await self._role(RoleId.STORY_ARCHITECT, "story_planning", project)
        else:
            await super()._story_planning(project)
            project.story_bible = StoryBible(synopsis=project.brief.story_description,
                immutable_facts=project.brief.must_preserve + project.brief.world_rules)

    async def _screenplay(self, project):
        if RoleId.SCREENWRITER in self.real_roles:
            await self._role(RoleId.SCREENWRITER, "screenplay", project)
        else:
            await super()._screenplay(project)
            await super()._scene_planning(project)
            project.screenplay = Screenplay(title=project.brief.title,
                characters=project.characters, locations=project.locations, props=project.props,
                preserved_constraints=project.brief.user_constraints + project.brief.must_preserve,
                immutable_facts=project.story_bible.immutable_facts,
                scenes=[ScreenplayScene(scene_id=s.scene_id, location_id=s.location_id,
                    title=s.title, action=[s.purpose], character_ids=s.character_ids, prop_ids=s.prop_ids)
                    for s in project.scenes])

    async def _bibles(self, project):
        if RoleId.VISUAL_DIRECTOR in self.real_roles:
            await self._role(RoleId.VISUAL_DIRECTOR, "bibles", project)
        else:
            story = project.story_bible
            await super()._bibles(project)
            project.story_bible = story

    async def _scene_planning(self, project):
        if RoleId.DIRECTOR in self.real_roles:
            await self._role(RoleId.DIRECTOR, "scene_planning", project)
        else:
            project.scenes = [Scene(scene_id=s.scene_id, title=s.title, purpose=" ".join(s.action),
                location_id=s.location_id, time_description="story time", character_ids=s.character_ids,
                prop_ids=s.prop_ids) for s in project.screenplay.scenes]

    async def _shot_planning(self, project):
        if RoleId.CINEMATOGRAPHER in self.real_roles:
            for scene in project.scenes:
                await self._role(RoleId.CINEMATOGRAPHER, "shot_planning", project, scene=scene)
        else:
            for scene in project.scenes:
                sid, chain_id = scene.scene_id + "_shot_1", scene.scene_id + "_chain"
                shot = Shot(shot_id=sid, scene_id=scene.scene_id,
                    narrative=ShotNarrative(purpose=scene.purpose, beat=scene.title),
                    duration_seconds=project.brief.target_duration / len(project.scenes),
                    camera=CameraSpec(shot_size=ShotSize.WIDE), lighting=LightingSpec(setup="Mock lighting"),
                    state_before=scene.initial_state, expected_state_after=scene.expected_final_state,
                    continuity_chain_id=chain_id, retry_budget=project.brief.max_retry,
                    quality_profile=project.brief.quality_level)
                self._commit(project, ShotPlan(scene_id=scene.scene_id, shots=[shot],
                    continuity_chains=[ContinuityChain(chain_id=chain_id, label=scene.title,
                        shot_ids=[sid], initial_state=scene.initial_state)],
                    preserved_constraints=project.brief.user_constraints + project.brief.must_preserve,
                    immutable_facts=project.story_bible.immutable_facts))

    def _checkpoint_extra(self):
        return {"runtime": "p2a", "real_roles": sorted(r.value for r in self.real_roles),
                "role_revision_history": [r.model_dump(mode="json") for r in self.role_revision_history],
                "contract_schema_revisions": [r.model_dump(mode="json") for r in self.contract_schema_revisions],
                "request_contract_type_revisions": [r.model_dump(mode="json")
                    for r in self.request_contract_type_revisions],
                "role_results": {key: result.model_dump(mode="json") for key, result in self.role_results.items()}}

    def _restore_extra(self, state):
        self.role_results = {key: RoleResult.model_validate(value) for key, value in state.get("role_results", {}).items()}
        self.real_roles = {RoleId(r) for r in state.get("real_roles", [r.value for r in RoleId])}
        self.role_revision_history = [RoleResult.model_validate(r) for r in state.get("role_revision_history", [])]
        self.contract_schema_revisions = [ContractSchemaRevision.model_validate(r)
            for r in state.get("contract_schema_revisions", [])]
        self.request_contract_type_revisions = [RequestContractTypeRevision.model_validate(r)
            for r in state.get("request_contract_type_revisions", [])]

    def authorize_request_contract_type_revision(self, scene_id: str,
                                                   authorization_reference: str) -> None:
        """Adopt Core-owned canonical state with one fresh bounded scene invocation."""
        project, graph = self._restore()
        if self.request_contract_type_revisions:
            raise ValueError("The request contract type revision has already been consumed")
        if not authorization_reference.strip():
            raise ValueError("An explicit authorization reference is required")
        key = RoleId.CINEMATOGRAPHER.value + ":" + scene_id
        previous = self.role_results.get(key)
        if not previous or previous.committed or previous.failure_code != "CONTRACT_STATE_SCOPE_CONFLICT":
            raise ValueError("Type revision requires the preserved Scene state-scope failure")
        scene = next((item for item in project.scenes if item.scene_id == scene_id), None)
        if scene is None:
            raise ValueError("Authorized Scene does not exist")
        definition = self.runner.registry.get(RoleId.CINEMATOGRAPHER)
        context = self.runner.context_builder.build(definition, project, scene=scene)
        schema = self.runner.adapter.response_format(ShotPlanDraft,
            max_scene_shots=context.payload["scene_shot_budget"], scene=scene)
        old_hash = (previous.invocation.contract_revision.request_schema_hash
                    if previous.invocation.contract_revision else content_hash({"target": previous.target_schema}))
        revision = RequestContractTypeRevision(authorization_reference=authorization_reference,
            scene_id=scene_id, parent_invocation_id=previous.invocation.invocation_id,
            parent_result_hash=content_hash(previous.model_dump(mode="json")), old_schema_hash=old_hash,
            new_schema_hash=content_hash(schema), mapper_version=CinematographerDraftMapper.version)
        invocation = RoleInvocation(invocation_id=previous.invocation.invocation_id + ":request_contract_type_revision",
            role_id=RoleId.CINEMATOGRAPHER, project_id=project.project_id, node_id="shot_planning",
            scene_id=scene_id, request_contract_revision=revision)
        self.role_revision_history.append(previous.model_copy(deep=True))
        self.request_contract_type_revisions.append(revision)
        self.role_results[key] = RoleResult(invocation=invocation, provider_id=self.llm_provider.provider_id,
            context=context, target_schema="ShotPlanDraft")
        artifact = self.artifact_store.create_placeholder(ArtifactType.TEXT,
            {"revision": revision.model_dump(mode="json"), "request_schema": schema},
            artifact_id="request_contract_type_revision_" + scene_id,
            source_job_id=invocation.invocation_id,
            provenance=Provenance(role=RoleId.CINEMATOGRAPHER.value,
                tool="REQUEST_CONTRACT_TYPE_REVISION", parameters={
                    "ownership_change": revision.ownership_change, "mapper_version": revision.mapper_version,
                    "parent_result_hash": revision.parent_result_hash,
                    "old_schema_hash": revision.old_schema_hash, "new_schema_hash": revision.new_schema_hash}))
        self._emit(EventType.ARTIFACT_CREATED, project.project_id,
            {"artifact_id": artifact.artifact_id, "version": artifact.version,
             "revision_kind": revision.kind, "scene_id": scene_id}, node_id="shot_planning")
        self._save_checkpoint(project, graph)

    def authorize_contract_schema_revision(self, scene_id: str, authorization_reference: str) -> None:
        """Consume one explicit extra allowance, narrowly scoped to an exhausted scene."""
        project, graph = self._restore()
        if self.contract_schema_revisions:
            raise ValueError("The one additional contract schema revision has already been authorized")
        if not authorization_reference.strip():
            raise ValueError("An explicit user authorization reference is required")
        key = RoleId.CINEMATOGRAPHER.value + ":" + scene_id
        previous = self.role_results.get(key)
        if not previous or previous.committed or previous.failure_code != "ROLE_OUTPUT_INVALID":
            raise ValueError("Only the specified exhausted, uncommitted Cinematographer scene may be revised")
        if not any(r.invocation.scene_id == scene_id and r.invocation.role_id == RoleId.CINEMATOGRAPHER
                   for r in self.role_revision_history):
            raise ValueError("Use the ordinary explicit revision allowance first")
        scene = next(s for s in project.scenes if s.scene_id == scene_id)
        definition = self.runner.registry.get(RoleId.CINEMATOGRAPHER)
        context = self.runner.context_builder.build(definition, project, scene=scene)
        schema = self.runner.adapter.response_format(ShotPlan,
            max_scene_shots=context.payload["scene_shot_budget"], scene=scene)
        revision = ContractSchemaRevision(authorization_reference=authorization_reference, scene_id=scene_id,
            previous_invocation_id=previous.invocation.invocation_id,
            previous_result_hash=content_hash(previous.model_dump(mode="json")), request_schema_hash=content_hash(schema))
        invocation = RoleInvocation(invocation_id=previous.invocation.invocation_id + ":contract_schema_revision",
            role_id=RoleId.CINEMATOGRAPHER, project_id=project.project_id, node_id="shot_planning",
            scene_id=scene_id, contract_revision=revision)
        self.role_revision_history.append(previous.model_copy(deep=True))
        self.contract_schema_revisions.append(revision)
        self.role_results[key] = RoleResult(invocation=invocation, provider_id=self.llm_provider.provider_id,
            context=context, target_schema="ShotPlan")
        artifact = self.artifact_store.create_placeholder(ArtifactType.TEXT,
            {"revision": revision.model_dump(mode="json"), "invocation_id": invocation.invocation_id,
             "request_schema": schema}, artifact_id="contract_schema_revision_" + scene_id,
            source_job_id=invocation.invocation_id,
            provenance=Provenance(role=RoleId.CINEMATOGRAPHER.value, tool="CONTRACT_SCHEMA_REVISION",
                parameters={"authorization": revision.model_dump(mode="json")}))
        self._emit(EventType.ARTIFACT_CREATED, project.project_id,
            {"artifact_id": artifact.artifact_id, "version": artifact.version,
             "revision_kind": revision.kind, "scene_id": scene_id}, node_id="shot_planning")
        self._save_checkpoint(project, graph)

    def revise_failed_role(self, role_id: str) -> None:
        """Explicit bounded revision after a diagnosed policy/schema change, keeping failed evidence."""
        project, graph = self._restore()
        failures = [(key, result) for key, result in self.role_results.items()
                    if result.invocation.role_id.value == role_id and result.failure_code == "ROLE_OUTPUT_INVALID"]
        if not failures:
            raise ValueError("No exhausted failed output exists for this role")
        if any(r.invocation.role_id.value == role_id for r in self.role_revision_history):
            raise ValueError("This role already used its one explicit revision; human intervention required")
        for key, result in failures:
            self.role_revision_history.append(result.model_copy(deep=True))
            del self.role_results[key]
        self._save_checkpoint(project, graph)

    def authorize_semantic_revision(self, scene_id: str, authorization_reference: str) -> None:
        """One explicit terminal repair invocation; retain the exhausted parent intact."""
        project, graph = self._restore()
        key = RoleId.CINEMATOGRAPHER.value + ':' + scene_id
        previous = self.role_results.get(key)
        if (not authorization_reference.strip() or not previous or previous.committed
                or previous.failure_code != 'ROLE_OUTPUT_INVALID' or not previous.pending_output):
            raise ValueError('Semantic revision requires an explicitly authorized exhausted scene')
        if previous.invocation.semantic_revision or any(
            r.invocation.semantic_revision and r.invocation.scene_id == scene_id
            for r in self.role_revision_history):
            raise ValueError('This scene semantic revision has already been consumed')
        scene = next(s for s in project.scenes if s.scene_id == scene_id)
        output, report = self.runner.validator.parse(ShotPlanDraft, previous.pending_output, project, scene=scene)
        if output is None or not is_terminal_only(report):
            raise ValueError('Targeted semantic revision requires only terminal continuity conflicts')
        schema = self.runner.adapter.response_format(TerminalRepairDraft)
        schema['json_schema']['schema']['$defs']['TerminalShotRepair']['properties']['shot_index']['const'] = len(output.shots) - 1
        revision = SemanticContractRevision(authorization_reference=authorization_reference,
            scene_id=scene_id, parent_invocation_id=previous.invocation.invocation_id,
            parent_result_hash=content_hash(previous.model_dump(mode='json')),
            source_output_hash=content_hash(previous.pending_output), request_schema_hash=content_hash(schema),
            terminal_target_hash=content_hash(scene.expected_final_state.model_dump(mode='json')))
        invocation = RoleInvocation(invocation_id=previous.invocation.invocation_id + ':semantic_contract_revision',
            role_id=RoleId.CINEMATOGRAPHER, project_id=project.project_id,
            node_id=previous.invocation.node_id, scene_id=scene_id, semantic_revision=revision)
        context = self.runner.context_builder.build(self.runner.registry.get(RoleId.CINEMATOGRAPHER), project, scene=scene)
        self.role_revision_history.append(previous.model_copy(deep=True))
        self.role_results[key] = RoleResult(invocation=invocation, provider_id=self.llm_provider.provider_id,
            context=context, target_schema='ShotPlanDraft', pending_output=previous.pending_output,
            terminal_repair_base=previous.pending_output)
        artifact = self.artifact_store.create_placeholder(ArtifactType.TEXT,
            {'revision': revision.model_dump(mode='json'), 'parent_result': previous.model_dump(mode='json'),
             'request_schema': schema}, artifact_id='semantic_contract_revision_' + scene_id,
            source_job_id=invocation.invocation_id,
            provenance=Provenance(role=RoleId.CINEMATOGRAPHER.value, tool=revision.kind,
                parameters={'revision': revision.model_dump(mode='json')}))
        self._emit(EventType.ARTIFACT_CREATED, project.project_id,
            {'artifact_id': artifact.artifact_id, 'version': artifact.version, 'revision_kind': revision.kind},
            node_id=invocation.node_id)
        self._save_checkpoint(project, graph)

    def _save_checkpoint(self, project, production):
        super()._save_checkpoint(project, production)
        self._emit(EventType.CHECKPOINT_CREATED, project.project_id,
            {"checkpoint_id": self._latest_checkpoint_id})
