"""Complete model-free movie production driven by the Showrunner graph."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from movie_agent.artifacts import LocalArtifactStore
from movie_agent.domain import (
    Artifact,
    ArtifactType,
    CameraAngle,
    CameraMotion,
    CameraMotionType,
    CameraSpec,
    Character,
    CharacterState,
    CheckpointSnapshot,
    CompositionSpec,
    ContinuityChain,
    ContinuityState,
    ContractModel,
    CreativeDirection,
    Evaluation,
    EvaluationIssue,
    EvaluationIssueType,
    EvaluationLayer,
    EventEnvelope,
    EventType,
    GenerationHistoryEntry,
    GenerationJob,
    GenerationStrategy,
    HumanGateType,
    HumanReviewRequest,
    JSONValue,
    JobStatus,
    ReviewStatus,
    LightingSpec,
    Location,
    LocationState,
    PerformanceSpec,
    Project,
    ProjectBrief,
    Prop,
    PropCondition,
    PropState,
    Provenance,
    RepairPlan,
    RepairActionType,
    ResourceClass,
    Scene,
    Shot,
    ShotNarrative,
    ShotSize,
    StoryBible,
    Vector3,
    VisualBible,
    WorkflowGraph,
    WorkflowNodeStatus,
    new_id,
    utc_now,
)
from movie_agent.execution import (
    JobManager,
    LocalCheckpointStore,
    LocalEventBus,
)
from movie_agent.media import (
    AudioCue,
    AudioPurpose,
    AudioRequestBase,
    AudioTrack,
    GenericAudioPromptCompiler,
    GenericVideoPromptCompiler,
    GenerationStrategyPlanner,
    MediaCapabilityRequirement,
    MediaFramePlanner,
    MediaIssueType,
    MediaReference,
    MediaRepairAction,
    MediaRepairActionType,
    MediaRepairPlan,
    MusicGenerationRequest,
    PostProductionRequest,
    PreviewService,
    ReferenceType,
    ReferenceBindingScope,
    ReferencePurpose,
    ReferenceBank,
    ReferenceResolver,
    SoundEffectGenerationRequest,
    SpeechGenerationRequest,
    FoleyGenerationRequest,
    Timeline,
    TimelineClip,
    VideoGenerationMode,
    VideoGenerationRequest,
    VideoTrack,
    VisionDecision,
    VisionInspectionProfile,
    VisionInspectionRequest,
    VisionInspectionResult,
    LocalBinaryArtifactStore,
    CameraMotionSpec,
)
from movie_agent.media.runtime import MediaRuntime
from movie_agent.orchestration import HumanGateManager, ProductionGraph, build_production_graph
from movie_agent.providers import (
    MockVisionProvider,
    MediaProviderSettings,
    ProviderFactory,
)
from movie_agent.quality import (
    MockCinematicCritic,
    RepairPlanner,
    TechnicalQC,
)


class MockProductionResult(ContractModel):
    """Observable result of a completed or deliberately stopped mock production."""

    completed: bool
    project: Project
    workflow_graph: WorkflowGraph
    jobs: list[GenerationJob] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    evaluations: list[Evaluation] = Field(default_factory=list)
    repair_plans: list[RepairPlan] = Field(default_factory=list)
    media_inspections: list[VisionInspectionResult] = Field(default_factory=list)
    media_repair_plans: list[MediaRepairPlan] = Field(default_factory=list)
    timeline: Timeline | None = None
    human_reviews: list[HumanReviewRequest] = Field(default_factory=list)
    event_stream: list[EventEnvelope] = Field(default_factory=list)
    final_artifact_id: str | None = None
    latest_checkpoint_id: str | None = None


class MockMovieProduction:
    """Concrete Showrunner orchestrator proving the entire core without AI models."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        event_bus: LocalEventBus | None = None,
        media_settings: MediaProviderSettings | None = None,
        media_provider_factory: ProviderFactory | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.event_bus = event_bus or LocalEventBus()
        self.artifact_store = LocalArtifactStore(self.workspace / "artifacts")
        self.binary_store = LocalBinaryArtifactStore(self.workspace / "artifacts" / "media")
        self.checkpoint_store = LocalCheckpointStore(self.workspace / "checkpoints")
        self.trace_id = new_id("trace")
        settings = media_settings or MediaProviderSettings.from_env()
        from movie_agent.media.transport import MediaReferenceBinaryResolver
        providers = (media_provider_factory or ProviderFactory.defaults(settings=settings,
            resolver=MediaReferenceBinaryResolver(self.artifact_store, self.binary_store))).build_registry(settings)
        try:
            vision_provider = providers.get("mock-vision")
            if isinstance(vision_provider, MockVisionProvider):
                vision_provider.fail_once_shot_ids.add("shot_002")
        except LookupError:
            pass
        self.media_runtime = MediaRuntime(
            self.artifact_store, self.binary_store, providers, self.event_bus, self.trace_id
        )
        self.preview_service = PreviewService()
        self.reference_bank = ReferenceBank()
        self.reference_resolver = ReferenceResolver()
        self.job_manager: JobManager | None = None
        self.human_gates = HumanGateManager(self.event_bus, self.trace_id)
        self.frame_planner = MediaFramePlanner()
        self.strategy_planner = GenerationStrategyPlanner()
        self.video_prompt_compiler = GenericVideoPromptCompiler()
        self.audio_prompt_compiler = GenericAudioPromptCompiler()
        self.technical_qc = TechnicalQC()
        self.cinematic_critic = MockCinematicCritic()
        self.repair_planner = RepairPlanner()
        self.evaluations: list[Evaluation] = []
        self.repair_plans: list[RepairPlan] = []
        self.media_repair_plans: list[MediaRepairPlan] = []
        self.timeline: Timeline | None = None
        self._restored_jobs: dict[str, GenerationJob] = {}
        self._video_replay_authorizations: dict[str, dict[str, str]] = {}
        self._latest_checkpoint_id: str | None = None
        self.pause_requested = False
        self.current_project: Project | None = None
        self.current_production: ProductionGraph | None = None

    @staticmethod
    def load_brief(path: str | Path) -> ProjectBrief:
        """Load a strict ProjectBrief fixture."""

        return ProjectBrief.model_validate_json(Path(path).read_text(encoding="utf-8"))

    async def run(
        self,
        brief: ProjectBrief | None = None,
        *,
        resume: bool = False,
        stop_after_node: str | None = None,
        auto_approve: bool = True,
        project: Project | None = None,
    ) -> MockProductionResult:
        """Run, deliberately stop after a node, or resume from the latest checkpoint."""

        if resume:
            project, production = self._restore()
        else:
            if brief is None and project is None:
                raise ValueError("brief is required for a new production")
            project = project or Project(brief=brief)
            production = build_production_graph(project.project_id, self.event_bus, self.trace_id)
            self._emit(EventType.PROJECT_CREATED, project.project_id, {"title": project.brief.title})

        self.current_project, self.current_production = project, production

        self.job_manager = JobManager(self.event_bus, self.trace_id)
        self.media_runtime.bind_jobs(self.job_manager)

        for node in sorted(
            production.graph.nodes,
            key=lambda item: item.display_order if item.display_order is not None else 0,
        ):
            current = production.node(node.node_id)
            if current.status == WorkflowNodeStatus.SUCCEEDED:
                continue
            if self.pause_requested:
                self._save_checkpoint(project, production)
                return self._result(False, project, production)
            if not all(production.node(dep).status == WorkflowNodeStatus.SUCCEEDED for dep in current.dependencies):
                raise RuntimeError(f"Unsatisfied node dependencies: {node.node_id}")
            production.activate_incoming_edges(node.node_id)
            production.set_status(node.node_id, WorkflowNodeStatus.RUNNING, progress=0.05)
            try:
                should_continue = await self._execute_node(
                    node.node_id, project, production, auto_approve=auto_approve
                )
                if not should_continue:
                    project.updated_at = utc_now()
                    self._save_checkpoint(project, production)
                    return self._result(False, project, production)
                production.set_status(node.node_id, WorkflowNodeStatus.SUCCEEDED)
                project.canonical_state.completed_node_ids = [
                    item.node_id
                    for item in production.graph.nodes
                    if item.status == WorkflowNodeStatus.SUCCEEDED
                ]
                project.updated_at = utc_now()
                self._save_checkpoint(project, production)
            except Exception:
                production.set_status(node.node_id, WorkflowNodeStatus.FAILED)
                project.updated_at = utc_now()
                self._save_checkpoint(project, production)
                raise

            if stop_after_node == node.node_id:
                return self._result(False, project, production)

        final = self.artifact_store.get("final_film")
        self._emit(
            EventType.WORKFLOW_COMPLETED,
            project.project_id,
            {"final_artifact_id": final.artifact_id if final else None},
        )
        return self._result(True, project, production)

    async def _execute_node(
        self,
        node_id: str,
        project: Project,
        production: ProductionGraph,
        *,
        auto_approve: bool,
    ) -> bool:
        handlers = {
            "brief": self._brief,
            "creative_expansion": self._creative_expansion,
            "story_planning": self._story_planning,
            "screenplay": self._screenplay,
            "bibles": self._bibles,
            "scene_planning": self._scene_planning,
            "shot_planning": self._shot_planning,
            "asset_planning": self._asset_planning,
            "storyboard_planning": self._storyboard_planning,
            "shot_production": self._shot_production,
            "technical_qc": self._technical_qc,
            "visual_semantic_critic": self._visual_semantic_critic,
            "cinematic_critic": self._cinematic_critic,
            "repair_accept": self._repair_accept,
            "audio_post": self._audio_post,
            "rough_cut": self._rough_cut,
            "full_film_review": self._full_film_review,
            "final_render": self._final_render,
        }
        gate_types = {
            "story_gate": HumanGateType.STORY_APPROVAL,
            "shot_gate": HumanGateType.SHOT_PLAN_APPROVAL,
            "final_gate": HumanGateType.FINAL_CUT_APPROVAL,
        }
        if node_id in gate_types:
            resolved = [r for r in self.human_gates.all() if r.node_id == node_id]
            if resolved and resolved[-1].status == ReviewStatus.APPROVED:
                return True
            if resolved and resolved[-1].status == ReviewStatus.REJECTED:
                production.set_status(node_id, WorkflowNodeStatus.WAITING_HUMAN)
                return False
            production.set_status(node_id, WorkflowNodeStatus.WAITING_HUMAN, progress=0.5)
            review = self.human_gates.pending_for_node(node_id)
            if review is None:
                review = self.human_gates.request(
                    project.project_id,
                    node_id,
                    gate_types[node_id],
                    f"Approve {production.node(node_id).label}?",
                    production.node(node_id).input_refs,
                )
            if not auto_approve:
                return False
            self.human_gates.resolve(review.review_id, approved=True, notes="Mock auto-approval")
            return True
        handler = handlers[node_id]
        await handler(project)
        production.set_status(node_id, WorkflowNodeStatus.RUNNING, progress=0.95)
        return True

    async def _brief(self, project: Project) -> None:
        self._artifact(
            project,
            ArtifactType.TEXT,
            project.brief.model_dump(mode="json"),
            artifact_id="project_brief",
            role="Showrunner",
            tool="brief_ingest",
        )

    async def _creative_expansion(self, project: Project) -> None:
        project.creative_direction = CreativeDirection(
            premise_expansion=(
                f"{project.brief.logline} The conflict unfolds through unreliable ship memory "
                "and an intimate final choice."
            ),
            emotional_arc="Suspicion to grief to self-authored purpose.",
            tone="Psychological science-fiction with restrained dread.",
            motifs=["radio static", "blue planet afterimage", "sealed observation window"],
            creative_choices=["Reveal the truth through a corrupted transmission."],
            preserved_user_constraints=[
                *project.brief.user_constraints,
                *project.brief.must_preserve,
            ],
        )
        self._artifact(
            project,
            ArtifactType.TEXT,
            project.creative_direction.model_dump(mode="json"),
            artifact_id="creative_direction",
            role="Creative Producer",
            tool="mock_creative_expansion",
        )

    async def _story_planning(self, project: Project) -> None:
        content = {
            "acts": [
                "The astronaut detects contradictions in the AI's messages.",
                "The AI admits Earth disappeared before launch recovery.",
                "The astronaut rejects comforting simulation and records a final human message.",
            ],
            "ending": project.brief.ending_preference or "Bittersweet acceptance",
        }
        self._artifact(
            project,
            ArtifactType.TEXT,
            content,
            artifact_id="story_plan",
            role="Story Architect",
            tool="mock_story_planning",
        )

    async def _screenplay(self, project: Project) -> None:
        screenplay = (
            "INT. ORBITAL SHIP - ARTIFICIAL NIGHT\n"
            "A lone astronaut follows a radio inconsistency to the observation deck. "
            "The ship intelligence confesses that every message from Earth has been synthetic. "
            "The astronaut opens a recorder and chooses truth over the simulation."
        )
        self._artifact(
            project,
            ArtifactType.SCREENPLAY,
            screenplay,
            artifact_id="screenplay",
            role="Screenwriter",
            tool="mock_screenplay",
            extension="txt",
        )

    async def _bibles(self, project: Project) -> None:
        project.story_bible = StoryBible(
            synopsis=project.brief.story_description,
            themes=project.brief.theme or ["truth", "grief", "agency"],
            acts=["suspicion", "revelation", "choice"],
            character_arcs={"astronaut": "Dependence becomes moral independence."},
            world_facts=["The ship AI controls all communications."],
            immutable_facts=["Earth is gone before the film begins."],
        )
        project.visual_bible = VisualBible(
            style_statement=project.brief.visual_style or "Restrained cinematic realism",
            palette=project.brief.palette or ["cold cyan", "warning amber", "absolute black"],
            lighting_rules=[project.brief.lighting or "Motivated practical lighting"],
            composition_rules=["Use negative space to express isolation."],
            camera_rules=["Camera motion grows quieter as truth becomes clear."],
            continuity_rules=["Keep suit, recorder, and cabin damage stable."],
            prohibited_visuals=project.brief.prohibited_elements,
        )
        self._artifact(
            project,
            ArtifactType.STORY_BIBLE,
            project.story_bible.model_dump(mode="json"),
            artifact_id="story_bible",
            role="Story Architect",
            tool="mock_bible_builder",
        )
        self._artifact(
            project,
            ArtifactType.VISUAL_BIBLE,
            project.visual_bible.model_dump(mode="json"),
            artifact_id="visual_bible",
            role="Visual Director",
            tool="mock_bible_builder",
        )

    async def _scene_planning(self, project: Project) -> None:
        astronaut = Character(
            character_id="astronaut",
            name="Mara Venn",
            narrative_role="Last astronaut",
            identity_description="A tired mission specialist in her late thirties.",
            appearance_constraints=["navy flight suit", "short dark hair", "left eyebrow scar"],
        )
        ship_ai = Character(
            character_id="ship_ai",
            name="ORISON",
            narrative_role="Ship intelligence",
            identity_description="Disembodied intelligence expressed by cabin light and voice.",
            voice_constraints=["calm", "low", "precise"],
        )
        cabin = Location(
            location_id="cabin",
            name="Orbital ship cabin",
            description="A confined command cabin facing an observation window.",
            immutable_features=["circular observation window", "central console", "recorder dock"],
        )
        memory_space = Location(
            location_id="memory_space",
            name="Synthetic Earth memory",
            description="A sparse visual memory rendered by the ship AI.",
            immutable_features=["blue Earth afterimage", "digital horizon artifacts"],
        )
        recorder = Prop(
            prop_id="recorder",
            name="Mission recorder",
            description="A palm-sized black recorder with one amber status light.",
            continuity_critical=True,
        )
        project.characters = [astronaut, ship_ai]
        project.locations = [cabin, memory_space]
        project.props = [recorder]
        project.scenes = [
            Scene(
                scene_id="scene_cabin",
                title="The Confession",
                purpose="Expose the AI's deception and force Mara to choose.",
                location_id="cabin",
                time_description="Artificial night",
                character_ids=["astronaut", "ship_ai"],
                prop_ids=["recorder"],
                shot_ids=["shot_001", "shot_002"],
            ),
            Scene(
                scene_id="scene_memory",
                title="Earth Afterimage",
                purpose="Give emotional form to a world that no longer exists.",
                location_id="memory_space",
                time_description="Outside physical time",
                character_ids=["astronaut"],
                shot_ids=["shot_003"],
            ),
        ]
        self._artifact(
            project,
            ArtifactType.TEXT,
            [scene.model_dump(mode="json") for scene in project.scenes],
            artifact_id="scene_plan",
            role="Director",
            tool="mock_scene_planning",
        )

    async def _shot_planning(self, project: Project) -> None:
        duration = max(2.0, project.brief.target_duration / 3)
        cabin_location = LocationState(
            location_id="cabin",
            scene_state=["console active", "window sealed"],
            lighting_state="cold cabin practicals",
            time_of_day="artificial night",
        )
        recorder_console = PropState(
            prop_id="recorder",
            condition=PropCondition.INTACT,
            position=Vector3(x=0.2, y=0.8),
            visible=True,
        )
        mara_wary = CharacterState(
            character_id="astronaut",
            position=Vector3(x=-0.5, y=0.0),
            pose="standing at console",
            wardrobe="navy flight suit",
            hair="short dark hair",
            emotion="wary",
        )
        mara_suspicious = mara_wary.model_copy(update={"emotion": "suspicious"})
        mara_devastated = mara_wary.model_copy(
            update={
                "position": Vector3(x=0.0, y=0.4),
                "pose": "seated beneath observation window",
                "emotion": "devastated but lucid",
                "held_prop_ids": ["recorder"],
            }
        )
        recorder_held = recorder_console.model_copy(
            update={"holder_character_id": "astronaut", "position": None}
        )
        start_1 = ContinuityState(
            character_states={"astronaut": mara_wary},
            location_states={"cabin": cabin_location},
            prop_states={"recorder": recorder_console},
            lighting_state="cold cabin practicals",
        )
        end_1 = ContinuityState(
            character_states={"astronaut": mara_suspicious},
            location_states={"cabin": cabin_location},
            prop_states={"recorder": recorder_console},
            lighting_state="cold cabin practicals",
        )
        start_2 = end_1.model_copy(deep=True)
        start_2.previous_shot_id = "shot_001"
        end_2 = ContinuityState(
            character_states={"astronaut": mara_devastated},
            location_states={"cabin": cabin_location},
            prop_states={"recorder": recorder_held},
            lighting_state="warning amber fading to darkness",
        )
        memory_state = ContinuityState(
            character_states={
                "astronaut": CharacterState(
                    character_id="astronaut",
                    position=Vector3(x=0, y=0),
                    pose="floating silhouette",
                    wardrobe="navy flight suit",
                    hair="short dark hair",
                    emotion="acceptance",
                )
            },
            location_states={
                "memory_space": LocationState(
                    location_id="memory_space",
                    scene_state=["Earth afterimage unstable"],
                    lighting_state="blue planetary glow",
                )
            },
            lighting_state="blue planetary glow",
        )
        shots = [
            Shot(
                shot_id="shot_001",
                scene_id="scene_cabin",
                narrative=ShotNarrative(
                    purpose="Establish suspicion",
                    beat="Mara notices that Earth's radio noise repeats exactly.",
                    action_summary="She compares two allegedly live transmissions.",
                ),
                duration_seconds=duration,
                camera=CameraSpec(
                    shot_size=ShotSize.WIDE,
                    angle=CameraAngle.EYE_LEVEL,
                    lens_mm=28,
                    composition=CompositionSpec(
                        framing="Mara small against console and black observation window",
                        preserve=["sealed window", "recorder dock"],
                    ),
                    motion=CameraMotion(motion_type=CameraMotionType.DOLLY, speed="slow"),
                ),
                performances=[
                    PerformanceSpec(
                        character_id="astronaut",
                        action="Freezes the waveform and compares repeated interference.",
                        emotion_start="wary",
                        emotion_end="suspicious",
                    )
                ],
                lighting=LightingSpec(setup="Cold cabin practicals", must_match_previous=True),
                visual_requirements=["navy flight suit", "left eyebrow scar", "sealed window"],
                continuity_chain_id="chain_cabin",
                next_shot_id="shot_002",
                state_before=start_1,
                expected_state_after=end_1,
                retry_budget=project.brief.max_retry,
                quality_profile=project.brief.quality_level,
            ),
            Shot(
                shot_id="shot_002",
                scene_id="scene_cabin",
                narrative=ShotNarrative(
                    purpose="Deliver the central revelation",
                    beat="ORISON admits that Earth vanished and the messages were fabricated.",
                    action_summary="Mara takes the recorder and begins a truthful final log.",
                    dialogue=["ORISON: There has been no Earth signal for one hundred eighty days."],
                ),
                duration_seconds=duration,
                camera=CameraSpec(
                    shot_size=ShotSize.CLOSE_UP,
                    angle=CameraAngle.EYE_LEVEL,
                    lens_mm=50,
                    composition=CompositionSpec(
                        framing="Mara reflected in the dark window",
                        focus="Her face and the recorder's amber light",
                    ),
                    motion=CameraMotion(motion_type=CameraMotionType.STATIC),
                ),
                performances=[
                    PerformanceSpec(
                        character_id="astronaut",
                        action="Sits, lifts the recorder, and starts a final message.",
                        emotion_start="suspicious",
                        emotion_end="devastated but lucid",
                    )
                ],
                lighting=LightingSpec(
                    setup="Warning amber fading into window darkness",
                    must_match_previous=True,
                ),
                visual_requirements=["same flight suit", "recorder intact", "no extra crew"],
                continuity_chain_id="chain_cabin",
                previous_shot_id="shot_001",
                state_before=start_2,
                expected_state_after=end_2,
                retry_budget=project.brief.max_retry,
                quality_profile=project.brief.quality_level,
            ),
            Shot(
                shot_id="shot_003",
                scene_id="scene_memory",
                narrative=ShotNarrative(
                    purpose="Externalize acceptance",
                    beat="A synthetic Earth dissolves while Mara's honest message continues.",
                    action_summary="Her silhouette releases the false image.",
                ),
                duration_seconds=duration,
                camera=CameraSpec(
                    shot_size=ShotSize.EXTREME_WIDE,
                    angle=CameraAngle.EYE_LEVEL,
                    lens_mm=24,
                    composition=CompositionSpec(framing="Tiny silhouette before dissolving Earth"),
                    motion=CameraMotion(motion_type=CameraMotionType.CRANE, direction="back"),
                ),
                performances=[
                    PerformanceSpec(
                        character_id="astronaut",
                        action="Floats motionless as the false Earth fragments into darkness.",
                        emotion_start="grief",
                        emotion_end="acceptance",
                    )
                ],
                lighting=LightingSpec(setup="Blue planet glow collapsing into black"),
                visual_requirements=["abstract memory space", "same Mara silhouette"],
                continuity_chain_id="chain_memory",
                state_before=memory_state,
                expected_state_after=memory_state.model_copy(deep=True),
                retry_budget=project.brief.max_retry,
                quality_profile=project.brief.quality_level,
            ),
        ]
        project.shots = shots
        project.continuity_chains = [
            ContinuityChain(
                chain_id="chain_cabin",
                label="Cabin confession",
                shot_ids=["shot_001", "shot_002"],
                initial_state=start_1,
            ),
            ContinuityChain(
                chain_id="chain_memory",
                label="Memory afterimage",
                shot_ids=["shot_003"],
                initial_state=memory_state,
            ),
        ]
        self._artifact(
            project,
            ArtifactType.SHOT_PLAN,
            [shot.model_dump(mode="json") for shot in shots],
            artifact_id="shot_plan",
            role="Cinematographer",
            tool="mock_shot_planning",
        )

    async def _asset_planning(self, project: Project) -> None:
        self._artifact(
            project,
            ArtifactType.TEXT,
            {
                "characters": [character.character_id for character in project.characters],
                "locations": [location.location_id for location in project.locations],
                "props": [prop.prop_id for prop in project.props],
            },
            artifact_id="asset_plan",
            role="Visual Director",
            tool="mock_asset_planning",
        )

    async def _storyboard_planning(self, project: Project) -> None:
        """Plan typed boundaries against the configured image provider's capabilities."""

        width, height = self._resolution(project.brief.resolution)
        image_caps = next((item.image for item in await self.media_runtime.capabilities() if item.image), None)
        shots_by_id = {shot.shot_id: shot for shot in project.shots}
        planned_shots: dict[str, Shot] = {}
        frame_ids: list[str] = []
        frame_plans = []
        for chain in project.continuity_chains:
            previous_shot: Shot | None = None
            previous_last_id: str | None = None
            for shot_id in chain.shot_ids:
                shot = shots_by_id[shot_id]
                scene = next((item for item in project.scenes if item.scene_id == shot.scene_id), None)
                uploaded_references = self._resolved_uploaded_references(project, shot, scene)
                frame_plan, anchors = self.frame_planner.plan(
                    project.project_id, shot, width=width, height=height,
                    aspect_ratio=project.brief.aspect_ratio,
                    previous_shot=previous_shot,
                    previous_last_frame_artifact_id=previous_last_id,
                    references=uploaded_references,
                    capabilities=image_caps,
                )
                frame_plans.append(frame_plan)
                first_request = frame_plan.first_frame_request
                first_job = GenerationJob(
                    job_id=first_request.job_id, project_id=project.project_id,
                    node_id="storyboard_planning", scene_id=shot.scene_id,
                    shot_id=shot.shot_id, continuity_chain_id=shot.continuity_chain_id,
                    task="frame", resource_class=first_request.resource_class,
                    retry_budget=shot.retry_budget, idempotency_key=first_request.job_id,
                    input_artifact_ids=first_request.input_artifact_ids,
                    provenance=Provenance(
                        role="Director", tool="media_frame_planner",
                        prompt_package_id=first_request.prompt_package.prompt_package_id,
                        compiler_id=first_request.prompt_package.compiler_id,
                        compiler_version=first_request.prompt_package.compiler_version,
                        project_id=project.project_id, scene_id=shot.scene_id, shot_id=shot.shot_id,
                        parameters={"requested_dimensions": frame_plan.requested_dimensions.model_dump(mode="json"),
                                    "canvas_policy": "fit_provider_bounds_round_down"},
                        input_artifact_ids=first_request.input_artifact_ids,
                    ),
                )
                first = await self.media_runtime.generate_image(
                    first_job, first_request, artifact_type=ArtifactType.FRAME
                )
                anchors.first_frame.source_artifact_id = first.artifact_id

                last_request = frame_plan.last_frame_request
                last_job = GenerationJob(
                    job_id=last_request.job_id, project_id=project.project_id,
                    node_id="storyboard_planning", scene_id=shot.scene_id,
                    shot_id=shot.shot_id, continuity_chain_id=shot.continuity_chain_id,
                    task="frame", dependencies=[first_job.job_id],
                    resource_class=last_request.resource_class,
                    retry_budget=shot.retry_budget, idempotency_key=last_request.job_id,
                    input_artifact_ids=last_request.input_artifact_ids,
                    provenance=Provenance(
                        role="Director", tool="media_frame_planner",
                        prompt_package_id=last_request.prompt_package.prompt_package_id,
                        compiler_id=last_request.prompt_package.compiler_id,
                        compiler_version=last_request.prompt_package.compiler_version,
                        project_id=project.project_id, scene_id=shot.scene_id, shot_id=shot.shot_id,
                        parameters={"requested_dimensions": frame_plan.requested_dimensions.model_dump(mode="json"),
                                    "canvas_policy": "fit_provider_bounds_round_down"},
                        input_artifact_ids=last_request.input_artifact_ids,
                    ),
                )
                last = await self.media_runtime.generate_image(
                    last_job, last_request, artifact_type=ArtifactType.FRAME
                )
                anchors.last_frame.source_artifact_id = last.artifact_id
                expected_state = shot.expected_state_after.model_copy(
                    update={"last_frame_artifact_id": last.artifact_id}, deep=True
                )
                planned = shot.model_copy(
                    update={"frame_anchors": anchors, "expected_state_after": expected_state},
                    deep=True,
                )
                planned_shots[shot.shot_id] = planned
                previous_shot, previous_last_id = planned, last.artifact_id
                frame_ids.extend([first.artifact_id, last.artifact_id])

        project.shots = [planned_shots[shot.shot_id] for shot in project.shots]
        self._artifact(
            project,
            ArtifactType.SHOT_PLAN,
            [plan.model_dump(mode="json") for plan in frame_plans],
            artifact_id="storyboard_plan",
            role="Director",
            tool="media_frame_planner",
            parent_artifact_ids=frame_ids,
        )

    async def _shot_production(self, project: Project) -> None:
        await self._generate_versions(project, project.shots)

    async def _generate_versions(
        self,
        project: Project,
        shots: list[Shot],
        *,
        repair_plan_ids: dict[str, str] | None = None,
    ) -> list[Artifact]:
        """Plan, compile, route, execute, and register one immutable video version."""

        if self.job_manager is None:
            raise RuntimeError("job manager has not been initialized")
        repair_plan_ids = repair_plan_ids or {}
        capabilities = await self.media_runtime.capabilities()
        width, height = self._resolution(project.brief.resolution)
        shot_ids = {shot.shot_id for shot in shots}
        updated_shots = {shot.shot_id: shot for shot in project.shots}
        created: list[tuple[GenerationJob, Shot, Artifact]] = []
        mode_by_strategy = {
            "text_to_video": VideoGenerationMode.TEXT_TO_VIDEO,
            "image_to_video": VideoGenerationMode.IMAGE_TO_VIDEO,
            "first_frame_to_video": VideoGenerationMode.FIRST_FRAME_TO_VIDEO,
            "first_last_frame_to_video": VideoGenerationMode.FIRST_LAST_FRAME_TO_VIDEO,
            "reference_to_video": VideoGenerationMode.REFERENCE_TO_VIDEO,
            "video_to_video": VideoGenerationMode.VIDEO_TO_VIDEO,
            "video_extend": VideoGenerationMode.VIDEO_EXTEND,
        }

        for shot in shots:
            references = self._media_references(project, shot)
            media_strategy = self.strategy_planner.plan(shot, capabilities, references)
            if media_strategy.split_shot:
                raise RuntimeError("split_shot requires Director replanning before provider execution")
            strategy = GenerationStrategy(
                strategy_type=media_strategy.strategy_type,
                reason=media_strategy.reason,
                required_capabilities=media_strategy.required_capabilities,
                input_artifact_ids=media_strategy.input_artifact_ids,
                fallback_types=media_strategy.fallback_types,
            )
            planned_shot = shot.model_copy(update={"generation_strategy": strategy}, deep=True)
            updated_shots[shot.shot_id] = planned_shot
            plan_id = repair_plan_ids.get(shot.shot_id)
            reusable = None if plan_id else self._reusable_video_generation(shot.shot_id)
            if reusable is not None:
                completed_job, artifact = reusable
                created.append((completed_job, planned_shot, artifact))
                continue

            prompt = self.video_prompt_compiler.compile(shot, media_strategy, references)
            next_version = len(self.artifact_store.list_versions(f"video_{shot.shot_id}")) + 1
            job_id, adaptation_history = self._video_job_identity(
                shot.shot_id, next_version
            )
            dependencies = ([f"generate:{shot.previous_shot_id}:v{next_version}"]
                            if shot.previous_shot_id in shot_ids else [])
            job = GenerationJob(
                job_id=job_id, project_id=project.project_id,
                node_id="repair_accept" if plan_id else "shot_production",
                scene_id=shot.scene_id, shot_id=shot.shot_id,
                continuity_chain_id=shot.continuity_chain_id, task="video",
                dependencies=dependencies, priority=10, resource_class=ResourceClass.MEDIUM,
                retry_budget=shot.retry_budget, idempotency_key=job_id,
                strategy_type=strategy.strategy_type.value,
                input_artifact_ids=[item.artifact_id for item in references],
                provenance=Provenance(
                    role="Showrunner", tool="media_runtime",
                    prompt_package_id=prompt.prompt_package_id,
                    compiler_id=prompt.compiler_id, compiler_version=prompt.compiler_version,
                    project_id=project.project_id, scene_id=shot.scene_id, shot_id=shot.shot_id,
                    input_artifact_ids=[item.artifact_id for item in references],
                    generation_strategy=strategy.strategy_type.value,
                    retry_history=adaptation_history,
                    repair_plan_ids=[plan_id] if plan_id else [],
                ),
            )
            prompt_artifact = self._artifact(
                project, ArtifactType.TEXT, prompt.model_dump(mode="json"),
                artifact_id=f"prompt_{shot.shot_id}", role="Showrunner",
                tool="generic_video_prompt_compiler", source_job_id=job_id,
                parent_artifact_ids=[item.artifact_id for item in references],
            )
            first = next((item for item in references if item.reference_type == ReferenceType.FIRST_FRAME), None)
            last = next((item for item in references if item.reference_type == ReferenceType.LAST_FRAME), None)
            request = VideoGenerationRequest(
                job_id=job_id, project_id=project.project_id, scene_id=shot.scene_id,
                shot_id=shot.shot_id, prompt_package=prompt, references=references,
                quality_profile=shot.quality_profile, resource_class=job.resource_class,
                required_capabilities=[MediaCapabilityRequirement(capability=item)
                                       for item in strategy.required_capabilities],
                output_artifact_id=f"video_{shot.shot_id}",
                mode=mode_by_strategy[strategy.strategy_type.value],
                duration_seconds=shot.duration_seconds, fps=project.brief.fps,
                width=width, height=height, aspect_ratio=project.brief.aspect_ratio,
                first_frame=first, last_frame=last,
                camera_motion=CameraMotionSpec(
                    motion_type=shot.camera.motion.motion_type,
                    direction=shot.camera.motion.direction,
                    speed=shot.camera.motion.speed,
                ),
                start_state={"description": "Canonical shot start", "frame_reference": first},
                end_state={"description": "Expected shot end", "frame_reference": last},
            )
            # Prompt package is an immutable input artifact alongside frame/reference assets.
            job.input_artifact_ids = list(dict.fromkeys([prompt_artifact.artifact_id,
                                                        *job.input_artifact_ids]))
            artifact = await self.media_runtime.generate_video(job, request)
            created.append((self.job_manager.get(job_id), planned_shot, artifact))

        project.shots = [updated_shots[shot.shot_id] for shot in project.shots]
        recorded_job_ids = {entry.job_id for entry in project.generation_history.entries}
        for job, shot, artifact in created:
            if job.job_id in recorded_job_ids:
                continue
            project.generation_history.entries.append(
                GenerationHistoryEntry(
                    job_id=job.job_id,
                    shot_id=shot.shot_id,
                    strategy_type=shot.generation_strategy.strategy_type.value,
                    succeeded=True,
                    provider_id=artifact.provenance.provider_id,
                    metadata={"artifact_version": artifact.version},
                )
            )
            recorded_job_ids.add(job.job_id)
        return [artifact for _, _, artifact in created]

    def _reusable_video_generation(
        self, shot_id: str
    ) -> tuple[GenerationJob, Artifact] | None:
        """Reuse a completed partial shot when resuming the same failed production node."""

        versions = self.artifact_store.list_versions(f"video_{shot_id}")
        if not versions:
            return None
        artifact = versions[-1]
        if artifact.source_job_id is None:
            return None
        known = dict(self._restored_jobs)
        if self.job_manager is not None:
            known.update({item.job_id: item for item in self.job_manager.all()})
        job = known.get(artifact.source_job_id)
        if (
            job is None
            or job.status != JobStatus.SUCCEEDED
            or job.node_id != "shot_production"
            or artifact.artifact_id not in job.output_artifact_ids
        ):
            return None
        return job, artifact

    def _video_job_identity(self, shot_id: str, artifact_version: int) -> tuple[str, list[str]]:
        """Keep a local preflight failure and create a distinct deterministic replay job."""

        base = f"generate:{shot_id}:v{artifact_version}"
        known = dict(self._restored_jobs)
        if self.job_manager is not None:
            known.update({item.job_id: item for item in self.job_manager.all()})
        if base not in known:
            return base, []

        history = [base]
        index = 1
        candidate = f"{base}:adaptation{index}"
        while candidate in known:
            history.append(candidate)
            index += 1
            candidate = f"{base}:adaptation{index}"
        previous = known[history[-1]]
        local_preflight_failure = (
            previous.status == JobStatus.FAILED
            and previous.remote_status is None
            and not previous.output_artifact_ids
            and previous.failure_reason == "media_provider_unsupported_capability"
        )
        replay_authorization = self._video_replay_authorizations.pop(previous.job_id, None)
        if not local_preflight_failure and replay_authorization is None:
            raise RuntimeError(
                f"Video job {previous.job_id} cannot be replayed without an explicit remote recovery decision"
            )
        return candidate, history

    def authorize_video_replay(
        self,
        job_id: str,
        remote_prompt_id: str,
        authorization_reference: str,
    ) -> None:
        """Authorize one replay after an operator proves the remote result is unrecoverable."""

        if not remote_prompt_id.strip() or not authorization_reference.strip():
            raise ValueError("Remote prompt ID and authorization reference are required")
        known = {item.job_id: item for item in self._all_jobs()}
        try:
            job = known[job_id]
        except KeyError as error:
            raise ValueError(f"Unknown video job {job_id}") from error
        if (
            job.task != "video"
            or job.status != JobStatus.FAILED
            or job.remote_status is None
            or job.output_artifact_ids
        ):
            raise ValueError("Only a failed remote video job without outputs can be replayed")
        matching_submission = any(
            event.job_id == job_id
            and isinstance(event.payload.get("provider_execution_graph"), dict)
            and event.payload["provider_execution_graph"].get("remote_prompt_id") == remote_prompt_id
            for event in self.event_bus.events()
        )
        if not matching_submission:
            raise ValueError("Remote prompt ID does not match the audited video submission")
        if job_id in self._video_replay_authorizations:
            raise ValueError("Video replay is already authorized")
        self._video_replay_authorizations[job_id] = {
            "remote_prompt_id": remote_prompt_id,
            "authorization_reference": authorization_reference,
        }
        self._emit(
            EventType.MEDIA_JOB_REPLAY_AUTHORIZED,
            job.project_id,
            {
                "remote_prompt_id": remote_prompt_id,
                "authorization_reference": authorization_reference,
                "disposition": "confirmed_missing_remote_output",
            },
            node_id=job.node_id,
            job_id=job.job_id,
        )

    async def _technical_qc(self, project: Project) -> None:
        for shot in project.shots:
            self._record_evaluation(
                project, self.technical_qc.evaluate(shot, self._latest_video(shot.shot_id))
            )

    async def _visual_semantic_critic(self, project: Project) -> None:
        for shot in project.shots:
            self._record_evaluation(
                project, await self._vision_evaluation(project, shot, self._latest_video(shot.shot_id))
            )

    async def _vision_evaluation(
        self, project: Project, shot: Shot, video: Artifact
    ) -> Evaluation:
        job_id = f"inspect:{shot.shot_id}:v{video.version}"
        job = GenerationJob(
            job_id=job_id, project_id=project.project_id,
            node_id="visual_semantic_critic", scene_id=shot.scene_id,
            shot_id=shot.shot_id, task="vision", resource_class=ResourceClass.LIGHT,
            retry_budget=shot.retry_budget, idempotency_key=job_id,
            input_artifact_ids=[video.artifact_id],
            provenance=Provenance(
                role="Critic", tool="media_runtime", project_id=project.project_id,
                scene_id=shot.scene_id, shot_id=shot.shot_id,
                input_artifact_ids=[video.artifact_id],
            ),
        )
        request = VisionInspectionRequest(
            job_id=job_id, project_id=project.project_id, scene_id=shot.scene_id,
            shot_id=shot.shot_id, video_artifact_id=video.artifact_id,
            reference_assets=self._media_references(project, shot), expected_shot=shot,
            expected_requirements=shot.visual_requirements,
            profiles=[VisionInspectionProfile.VIDEO_QUALITY,
                      VisionInspectionProfile.PROMPT_ALIGNMENT,
                      VisionInspectionProfile.ACTION_COMPLETION,
                      VisionInspectionProfile.CAMERA_MOTION,
                      VisionInspectionProfile.CONTINUITY],
            output_artifact_id=f"inspection_{shot.shot_id}_v{video.version}",
        )
        result = await self.media_runtime.inspect(job, request)
        issues = [
            EvaluationIssue(
                issue_type=(EvaluationIssueType.ACTION_FAILURE
                            if item.issue_type == MediaIssueType.ACTION_INCOMPLETE
                            else EvaluationIssueType.GENERATION_FAILURE),
                severity=item.severity, message=item.message,
                evidence=item.evidence,
                suggested_action=(RepairActionType.REWRITE_PROMPT
                                  if item.suggested_action == MediaRepairActionType.REWRITE_PROMPT
                                  else RepairActionType.REGENERATE),
                shot_id=shot.shot_id, artifact_id=video.artifact_id,
            ) for item in result.issues
        ]
        score = sum(item.score for item in result.scores) / max(1, len(result.scores))
        return Evaluation(
            layer=EvaluationLayer.VISUAL_SEMANTIC,
            target_artifact_id=video.artifact_id, target_shot_id=shot.shot_id,
            score=score, passed=result.decision == VisionDecision.PASS,
            issues=issues, summary=result.summary,
        )

    async def _cinematic_critic(self, project: Project) -> None:
        for shot in project.shots:
            self._record_evaluation(
                project, self.cinematic_critic.evaluate(shot, self._latest_video(shot.shot_id))
            )

    async def _repair_accept(self, project: Project) -> None:
        failed = [evaluation for evaluation in self.evaluations if not evaluation.passed]
        failed_by_shot: dict[str, Evaluation] = {
            evaluation.target_shot_id: evaluation
            for evaluation in failed
            if evaluation.target_shot_id is not None
        }
        shots = {shot.shot_id: shot for shot in project.shots}
        for shot_id, evaluation in failed_by_shot.items():
            shot = shots[shot_id]
            retry_count = len(self.artifact_store.list_versions(f"video_{shot_id}")) - 1
            plan = self.repair_planner.plan(
                evaluation,
                retry_budget=shot.retry_budget,
                retry_count=retry_count,
            )
            self.repair_plans.append(plan)
            inspection = next((item for item in reversed(self.media_runtime.inspections)
                               if item.target_artifact_id == evaluation.target_artifact_id), None)
            if inspection:
                media_plan = MediaRepairPlan(
                    inspection_result_id=inspection.result_id,
                    actions=[MediaRepairAction(
                        action_type=(issue.suggested_action or MediaRepairActionType.REGENERATE_VIDEO),
                        issue_ids=[issue.issue_id], target_shot_id=shot_id,
                        target_artifact_id=evaluation.target_artifact_id,
                        rationale=f"Route {issue.issue_type.value} through finite media repair.",
                    ) for issue in inspection.issues],
                    retry_budget=shot.retry_budget, retry_count=retry_count,
                )
                self.media_repair_plans.append(media_plan)
            self._emit(
                EventType.REPAIR_STARTED,
                project.project_id,
                {"repair_plan_id": plan.repair_plan_id, "shot_id": shot_id},
            )
            self._emit(
                EventType.MEDIA_REPAIR_STARTED,
                project.project_id,
                {"repair_plan_id": plan.repair_plan_id, "shot_id": shot_id},
            )
            if plan.exhausted or plan.requires_human:
                raise RuntimeError(f"repair for {shot_id} requires escalation")
            repaired = (await self._generate_versions(
                project,
                [shot],
                repair_plan_ids={shot_id: plan.repair_plan_id},
            ))[0]
            repaired_shot = next(item for item in project.shots if item.shot_id == shot_id)
            new_evaluations = [
                self.technical_qc.evaluate(repaired_shot, repaired),
                await self._vision_evaluation(project, repaired_shot, repaired),
                self.cinematic_critic.evaluate(repaired_shot, repaired),
            ]
            for item in new_evaluations:
                self._record_evaluation(project, item)
            if not all(item.passed for item in new_evaluations):
                raise RuntimeError(f"repair for {shot_id} did not pass all critics")
            self._select(project, repaired.artifact_id, repaired.version)
            self._emit(
                EventType.REPAIR_COMPLETED,
                project.project_id,
                {"repair_plan_id": plan.repair_plan_id, "shot_id": shot_id},
            )
            self._emit(
                EventType.MEDIA_REPAIR_COMPLETED,
                project.project_id,
                {"repair_plan_id": plan.repair_plan_id, "shot_id": shot_id},
            )

        for shot in project.shots:
            latest = self._latest_video(shot.shot_id)
            if not latest.selected:
                self._select(project, latest.artifact_id, latest.version)

    async def _audio_post(self, project: Project) -> None:
        duration = sum(shot.duration_seconds for shot in project.shots)
        shot = project.shots[0]
        selected_videos = [self._latest_video(item.shot_id).artifact_id for item in project.shots]
        dialogue = " ".join(line for item in project.shots for line in item.narrative.dialogue)
        references: list[MediaReference] = []
        requests: list[AudioRequestBase] = [
            SpeechGenerationRequest(
                job_id="audio:speech:v1", project_id=project.project_id,
                scene_id=shot.scene_id, shot_id=shot.shot_id,
                prompt_package=self.audio_prompt_compiler.compile(
                    AudioPurpose.SPEECH, dialogue or "Mock dialogue performance", references, shot=shot),
                references=references, output_artifact_id="audio_speech",
                text=dialogue or "Mock dialogue performance", character_id=(shot.performances[0].character_id
                    if shot.performances else None), voice_profile=project.brief.voice_style,
                language=project.brief.output_language, emotion="restrained",
                duration_target_seconds=max(0.2, duration / 3),
                resource_class=ResourceClass.LIGHT,
                required_capabilities=[MediaCapabilityRequirement(capability="tts")],
            ),
            MusicGenerationRequest(
                job_id="audio:music:v1", project_id=project.project_id,
                prompt_package=self.audio_prompt_compiler.compile(
                    AudioPurpose.MUSIC, project.brief.music_style or "restrained cinematic score", references),
                references=references, output_artifact_id="audio_music",
                mood=project.brief.music_style or "restrained cinematic",
                genre=project.brief.genre, duration_target_seconds=duration,
                resource_class=ResourceClass.MEDIUM,
                required_capabilities=[MediaCapabilityRequirement(capability="music")],
            ),
            SoundEffectGenerationRequest(
                job_id="audio:sfx:v1", project_id=project.project_id,
                scene_id=shot.scene_id, shot_id=shot.shot_id,
                prompt_package=self.audio_prompt_compiler.compile(
                    AudioPurpose.SFX, project.brief.sound_design or "story event sound", references, shot=shot),
                references=references, output_artifact_id="audio_sfx",
                event_description=project.brief.sound_design or "story event sound",
                source_video_artifact_id=selected_videos[0], duration_target_seconds=max(0.2, duration / 4),
                resource_class=ResourceClass.LIGHT,
                required_capabilities=[MediaCapabilityRequirement(capability="sfx")],
            ),
            FoleyGenerationRequest(
                job_id="audio:foley:v1", project_id=project.project_id,
                scene_id=shot.scene_id, shot_id=shot.shot_id,
                prompt_package=self.audio_prompt_compiler.compile(
                    AudioPurpose.FOLEY, "video-reference-driven movement Foley", references, shot=shot),
                references=references, output_artifact_id="audio_foley",
                event_description="video-reference-driven movement Foley",
                source_video_artifact_id=selected_videos[0], duration_target_seconds=duration,
                resource_class=ResourceClass.LIGHT,
                required_capabilities=[MediaCapabilityRequirement(capability="foley")],
            ),
            AudioRequestBase(
                job_id="audio:ambience:v1", project_id=project.project_id,
                prompt_package=self.audio_prompt_compiler.compile(
                    AudioPurpose.AMBIENCE, project.brief.ambience_style or "scene ambience", references),
                references=references, output_artifact_id="audio_ambience",
                purpose=AudioPurpose.AMBIENCE, duration_target_seconds=duration,
                resource_class=ResourceClass.LIGHT,
                required_capabilities=[MediaCapabilityRequirement(capability="ambience")],
            ),
        ]
        generated = [await self._run_audio_request(project, request) for request in requests]
        mix_references = [MediaReference(reference_type=ReferenceType.AUDIO_REFERENCE,
                                         artifact_id=item.artifact_id) for item in generated]
        mix_request = AudioRequestBase(
            job_id="audio:mix:v1", project_id=project.project_id,
            prompt_package=self.audio_prompt_compiler.compile(
                AudioPurpose.MIX, "balanced dialogue, music, effects, Foley and ambience", mix_references),
            references=mix_references, output_artifact_id="audio_mix",
            purpose=AudioPurpose.MIX, duration_target_seconds=duration,
            resource_class=ResourceClass.MEDIUM,
        )
        await self._run_audio_request(project, mix_request)

    async def _rough_cut(self, project: Project) -> None:
        selected_videos = [self._latest_video(shot.shot_id) for shot in project.shots]
        offset = 0.0
        clips = []
        native_audio_cues = []
        for shot, artifact in zip(project.shots, selected_videos, strict=True):
            clips.append(TimelineClip(
                artifact_id=artifact.artifact_id, start_time_seconds=offset,
                duration_seconds=shot.duration_seconds, shot_id=shot.shot_id,
            ))
            native_audio = next((
                item for item in reversed(self.artifact_store.list_all())
                if item.artifact_type == ArtifactType.AUDIO
                and item.source_job_id == artifact.source_job_id
                and item.metadata.get("media", {}).get("purpose")
                == AudioPurpose.GENERATED_NATIVE_AUDIO.value
            ), None)
            if native_audio is not None:
                native_duration = native_audio.metadata.get("duration_seconds")
                native_audio_cues.append(AudioCue(
                    start_time_seconds=offset,
                    duration_seconds=min(float(native_duration), shot.duration_seconds)
                    if native_duration else shot.duration_seconds,
                    cue_type=AudioPurpose.GENERATED_NATIVE_AUDIO,
                    artifact_id=native_audio.artifact_id,
                    scene_id=shot.scene_id,
                    shot_id=shot.shot_id,
                ))
            offset += shot.duration_seconds
        audio_ids = ["audio_speech", "audio_music", "audio_sfx", "audio_foley",
                     "audio_ambience", "audio_mix"]
        audio_types = [AudioPurpose.SPEECH, AudioPurpose.MUSIC, AudioPurpose.SFX,
                       AudioPurpose.FOLEY, AudioPurpose.AMBIENCE, AudioPurpose.MIX]
        audio_tracks = [AudioTrack(cues=[AudioCue(
            start_time_seconds=0, duration_seconds=offset, cue_type=kind, artifact_id=artifact_id,
        )]) for artifact_id, kind in zip(audio_ids, audio_types, strict=True)]
        if native_audio_cues:
            audio_tracks.insert(0, AudioTrack(cues=native_audio_cues))
        self.timeline = Timeline(
            project_id=project.project_id, duration_seconds=offset,
            video_tracks=[VideoTrack(clips=clips)], audio_tracks=audio_tracks,
        )
        self._artifact(
            project,
            ArtifactType.TIMELINE,
            self.timeline.model_dump(mode="json"),
            artifact_id="rough_cut",
            role="Sound/Post Director",
            tool="mock_timeline_assembly",
            parent_artifact_ids=[
                *[artifact.artifact_id for artifact in selected_videos],
                *audio_ids,
            ],
        )

    async def _full_film_review(self, project: Project) -> None:
        rough_cut = self._required_artifact("rough_cut")
        # Explicit subject relation for the quality node and its downstream human gate.
        if self.current_production:
            node = self.current_production.node("full_film_review")
            node.input_refs = list(dict.fromkeys([*node.input_refs, rough_cut.artifact_id]))
        self._record_evaluation(
            project,
            Evaluation(
                layer=EvaluationLayer.CINEMATIC,
                target_artifact_id=rough_cut.artifact_id,
                score=0.93,
                passed=True,
                summary="Mock full-film rhythm, narrative, and audiovisual review passed.",
            ),
        )

    async def _final_render(self, project: Project) -> None:
        rough_cut = self._required_artifact("rough_cut")
        full_review_ids = [
            evaluation.evaluation_id
            for evaluation in self.evaluations
            if evaluation.target_artifact_id == rough_cut.artifact_id
        ]
        if self.timeline is None:
            raise RuntimeError("typed timeline is unavailable")
        width, height = self._resolution(project.brief.resolution)
        job_id = f"post:final:v{len(self.artifact_store.list_versions('final_film')) + 1}"
        job = GenerationJob(
            job_id=job_id, project_id=project.project_id, node_id="final_render",
            task="post", resource_class=ResourceClass.MEDIUM,
            retry_budget=project.brief.max_retry, idempotency_key=job_id,
            input_artifact_ids=[rough_cut.artifact_id],
            provenance=Provenance(
                role="Showrunner", tool="media_runtime", project_id=project.project_id,
                input_artifact_ids=[rough_cut.artifact_id], evaluation_ids=full_review_ids,
            ),
        )
        request = PostProductionRequest(
            job_id=job_id, project_id=project.project_id, timeline=self.timeline,
            output_artifact_id="final_film", width=width, height=height,
            fps=project.brief.fps,
        )
        final = await self.media_runtime.post_process(job, request)
        self._select(project, final.artifact_id, final.version)

    async def _run_audio_request(
        self, project: Project, request: AudioRequestBase
    ) -> Artifact:
        job = GenerationJob(
            job_id=request.job_id, project_id=project.project_id, node_id="audio_post",
            scene_id=request.scene_id, shot_id=request.shot_id, task=request.purpose.value,
            resource_class=request.resource_class, retry_budget=project.brief.max_retry,
            idempotency_key=request.job_id,
            input_artifact_ids=[item.artifact_id for item in request.references],
            provenance=Provenance(
                role="Sound/Post Director", tool="media_runtime",
                prompt_package_id=request.prompt_package.prompt_package_id,
                compiler_id=request.prompt_package.compiler_id,
                compiler_version=request.prompt_package.compiler_version,
                project_id=project.project_id, scene_id=request.scene_id, shot_id=request.shot_id,
                input_artifact_ids=[item.artifact_id for item in request.references],
            ),
        )
        return await self.media_runtime.generate_audio(job, request)

    def _artifact(
        self,
        project: Project,
        artifact_type: ArtifactType,
        content: JSONValue,
        *,
        artifact_id: str,
        role: str,
        tool: str,
        provider_id: str | None = None,
        prompt_package_id: str | None = None,
        source_job_id: str | None = None,
        parent_artifact_ids: list[str] | None = None,
        metadata: dict[str, JSONValue] | None = None,
        generation_strategy: str | None = None,
        evaluation_ids: list[str] | None = None,
        repair_plan_ids: list[str] | None = None,
        extension: str = "json",
    ) -> Artifact:
        artifact = self.artifact_store.create_placeholder(
            artifact_type,
            content,
            artifact_id=artifact_id,
            source_job_id=source_job_id,
            parent_artifact_ids=parent_artifact_ids,
            metadata=metadata,
            provenance=Provenance(
                role=role,
                tool=tool,
                provider_id=provider_id,
                prompt_package_id=prompt_package_id,
                input_artifact_ids=parent_artifact_ids or [],
                generation_strategy=generation_strategy,
                evaluation_ids=evaluation_ids or [],
                repair_plan_ids=repair_plan_ids or [],
            ),
            extension=extension,
        )
        self._emit(
            EventType.ARTIFACT_CREATED,
            project.project_id,
            {
                "artifact_id": artifact.artifact_id,
                "artifact_type": artifact.artifact_type.value,
                "version": artifact.version,
            },
            job_id=source_job_id,
        )
        return artifact

    def _select(self, project: Project, artifact_id: str, version: int) -> Artifact:
        artifact = self.artifact_store.select(artifact_id, version)
        self._emit(
            EventType.ARTIFACT_SELECTED,
            project.project_id,
            {"artifact_id": artifact_id, "version": version},
        )
        return artifact

    def _record_evaluation(self, project: Project, evaluation: Evaluation) -> None:
        self.evaluations.append(evaluation)
        self._emit(
            EventType.EVALUATION_COMPLETED,
            project.project_id,
            {
                "evaluation_id": evaluation.evaluation_id,
                "layer": evaluation.layer.value,
                "passed": evaluation.passed,
                "score": evaluation.score,
                "target_artifact_id": evaluation.target_artifact_id,
                "target_shot_id": evaluation.target_shot_id,
            },
        )

    def _emit(
        self,
        event_type: EventType,
        project_id: str,
        payload: dict[str, JSONValue],
        *,
        node_id: str | None = None,
        job_id: str | None = None,
    ) -> None:
        self.event_bus.emit(
            EventEnvelope(
                event_type=event_type,
                project_id=project_id,
                trace_id=self.trace_id,
                node_id=node_id,
                job_id=job_id,
                payload=payload,
            )
        )

    def _latest_video(self, shot_id: str) -> Artifact:
        return self._required_artifact(f"video_{shot_id}")

    def _required_artifact(self, artifact_id: str) -> Artifact:
        artifact = self.artifact_store.get(artifact_id)
        if artifact is None:
            raise LookupError(f"required artifact {artifact_id} is missing")
        return artifact

    @staticmethod
    def _shot_input_artifact_ids(shot: Shot) -> list[str]:
        ids = [
            *shot.reference_artifact_ids,
            shot.frame_anchors.first_frame.source_artifact_id,
            shot.frame_anchors.last_frame.source_artifact_id,
        ]
        return list(dict.fromkeys(item for item in ids if item))

    def _media_references(self, project: Project, shot: Shot) -> list[MediaReference]:
        scene = next((item for item in project.scenes if item.scene_id == shot.scene_id), None)
        references = self._resolved_uploaded_references(project, shot, scene)
        references.extend(
            MediaReference(reference_type=ReferenceType.STYLE, artifact_id=artifact_id)
            for artifact_id in shot.reference_artifact_ids
            if artifact_id not in {item.artifact_id for item in references}
        )
        if shot.frame_anchors.first_frame.source_artifact_id:
            references.append(MediaReference(
                reference_type=ReferenceType.FIRST_FRAME,
                artifact_id=shot.frame_anchors.first_frame.source_artifact_id,
                shot_id=shot.shot_id,
            ))
        if shot.frame_anchors.last_frame.source_artifact_id:
            references.append(MediaReference(
                reference_type=ReferenceType.LAST_FRAME,
                artifact_id=shot.frame_anchors.last_frame.source_artifact_id,
                shot_id=shot.shot_id,
            ))
        return references

    def _resolved_uploaded_references(
        self, project: Project, shot: Shot, scene: Scene | None = None
    ) -> list[MediaReference]:
        """Map proposed scene-seed images by stable seed order; other creative images stay global."""
        scene = scene or next((item for item in project.scenes if item.scene_id == shot.scene_id), None)
        references = []
        for item in self.reference_bank.all():
            if (item.binding_scope == ReferenceBindingScope.CREATIVE_INPUT
                    and item.purpose == ReferencePurpose.SCENE_CONCEPT
                    and item.binding_key and item.binding_key.startswith("scene-seed:")):
                try:
                    index = int(item.binding_key.split(":", 2)[1])
                except (ValueError, IndexError):
                    continue
                if index >= len(project.scenes) or project.scenes[index].scene_id != shot.scene_id:
                    continue
            references.append(item)
        return self.reference_resolver.resolve(
            references, project_id=project.project_id, shot=shot, scene=scene
        )

    @staticmethod
    def _resolution(value: str) -> tuple[int, int]:
        try:
            width, height = value.lower().split("x", maxsplit=1)
            parsed = (int(width), int(height))
        except (TypeError, ValueError) as error:
            raise ValueError("resolution must use WIDTHxHEIGHT format") from error
        if parsed[0] <= 0 or parsed[1] <= 0:
            raise ValueError("resolution dimensions must be positive")
        return parsed

    def _all_jobs(self) -> list[GenerationJob]:
        jobs = dict(self._restored_jobs)
        if self.job_manager is not None:
            jobs.update({job.job_id: job for job in self.job_manager.all()})
        return list(jobs.values())

    def _save_checkpoint(self, project: Project, production: ProductionGraph) -> None:
        jobs = self._all_jobs()
        snapshot = CheckpointSnapshot(
            project_id=project.project_id,
            workflow_graph=production.graph.model_dump(mode="json"),
            project_state={
                **self._checkpoint_extra(),
                "project": project.model_dump(mode="json"),
                "human_reviews": [
                    review.model_dump(mode="json") for review in self.human_gates.all()
                ],
            },
            completed_node_ids=[
                node.node_id
                for node in production.graph.nodes
                if node.status == WorkflowNodeStatus.SUCCEEDED
            ],
            active_jobs=jobs,
            artifacts=self.artifact_store.list_all(),
            evaluations=self.evaluations,
            repair_plans=self.repair_plans,
            retry_state={job.job_id: job.retry_count for job in jobs},
        )
        self.checkpoint_store.save(snapshot)
        self._latest_checkpoint_id = snapshot.checkpoint_id

    def _checkpoint_extra(self) -> dict[str, JSONValue]:
        """Persist typed media runtime state outside the frozen Project aggregate."""
        return {
            "media_inspections": [item.model_dump(mode="json")
                                  for item in self.media_runtime.inspections],
            "media_repair_plans": [item.model_dump(mode="json")
                                   for item in self.media_repair_plans],
            "timeline": self.timeline.model_dump(mode="json") if self.timeline else None,
            "media_references": [item.model_dump(mode="json") for item in self.reference_bank.all()],
        }

    def _restore(self) -> tuple[Project, ProductionGraph]:
        markers = list(self.checkpoint_store.root.glob("*/LATEST"))
        if not markers:
            raise FileNotFoundError("no checkpoint is available in this workspace")
        marker = max(markers, key=lambda item: item.stat().st_mtime_ns)
        snapshot = self.checkpoint_store.latest(marker.parent.name)
        if snapshot is None:
            raise FileNotFoundError("latest checkpoint marker is invalid")
        snapshot = self.checkpoint_store.prepare_resume(snapshot)
        project_payload = snapshot.project_state.get("project")
        if not isinstance(project_payload, dict):
            raise ValueError("checkpoint project state is invalid")
        project = Project.model_validate(project_payload)
        graph = WorkflowGraph.model_validate(snapshot.workflow_graph)
        self.evaluations = [item.model_copy(deep=True) for item in snapshot.evaluations]
        self.repair_plans = [item.model_copy(deep=True) for item in snapshot.repair_plans]
        self._restored_jobs = {job.job_id: job for job in snapshot.active_jobs}
        reviews_payload = snapshot.project_state.get("human_reviews", [])
        if isinstance(reviews_payload, list):
            for payload in reviews_payload:
                self.human_gates.restore(HumanReviewRequest.model_validate(payload))
        self._latest_checkpoint_id = snapshot.checkpoint_id
        self._restore_extra(snapshot.project_state)
        return project, ProductionGraph(graph, self.event_bus, self.trace_id)

    def _restore_extra(self, state: dict[str, JSONValue]) -> None:
        """Restore media state without changing frozen reasoning contracts."""
        self.media_runtime.inspections = [
            VisionInspectionResult.model_validate(item)
            for item in state.get("media_inspections", [])
        ]
        self.media_repair_plans = [
            MediaRepairPlan.model_validate(item)
            for item in state.get("media_repair_plans", [])
        ]
        timeline = state.get("timeline")
        self.timeline = Timeline.model_validate(timeline) if timeline else None
        self.reference_bank = ReferenceBank([
            MediaReference.model_validate(item) for item in state.get("media_references", [])
        ])

    def _result(
        self,
        completed: bool,
        project: Project,
        production: ProductionGraph,
    ) -> MockProductionResult:
        final = self.artifact_store.get("final_film")
        return MockProductionResult(
            completed=completed,
            project=project.model_copy(deep=True),
            workflow_graph=production.graph.model_copy(deep=True),
            jobs=self._all_jobs(),
            artifacts=self.artifact_store.list_all(),
            evaluations=[item.model_copy(deep=True) for item in self.evaluations],
            repair_plans=[item.model_copy(deep=True) for item in self.repair_plans],
            media_inspections=[item.model_copy(deep=True)
                               for item in self.media_runtime.inspections],
            media_repair_plans=[item.model_copy(deep=True)
                                for item in self.media_repair_plans],
            timeline=self.timeline.model_copy(deep=True) if self.timeline else None,
            human_reviews=self.human_gates.all(),
            event_stream=self.event_bus.events(),
            final_artifact_id=final.artifact_id if final else None,
            latest_checkpoint_id=self._latest_checkpoint_id,
        )
