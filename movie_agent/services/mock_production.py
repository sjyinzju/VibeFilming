"""Complete model-free movie production driven by the Showrunner graph."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from movie_agent.artifacts import LocalArtifactStore
from movie_agent.cinematic import (
    GenerationStrategyPlanner,
    GenericPromptCompiler,
    RuleBasedFramePlanner,
    derive_next_state,
)
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
    EvaluationLayer,
    EventEnvelope,
    EventType,
    GenerationHistoryEntry,
    GenerationJob,
    GenerationRequest,
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
    ProviderKind,
    ProviderRequest,
    ProviderResult,
    RepairPlan,
    ResourceClass,
    RoutingRequest,
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
    LocalJobScheduler,
    LogicalResourceScheduler,
)
from movie_agent.orchestration import HumanGateManager, ProductionGraph, build_production_graph
from movie_agent.providers import MockProvider, ModelRouter
from movie_agent.quality import (
    MockCinematicCritic,
    MockVisualSemanticCritic,
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
    human_reviews: list[HumanReviewRequest] = Field(default_factory=list)
    event_stream: list[EventEnvelope] = Field(default_factory=list)
    final_artifact_id: str | None = None
    latest_checkpoint_id: str | None = None


class MockMovieProduction:
    """Concrete Showrunner orchestrator proving the entire core without AI models."""

    def __init__(self, workspace: str | Path, *, event_bus: LocalEventBus | None = None) -> None:
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.event_bus = event_bus or LocalEventBus()
        self.artifact_store = LocalArtifactStore(self.workspace / "artifacts")
        self.checkpoint_store = LocalCheckpointStore(self.workspace / "checkpoints")
        self.provider = MockProvider("mock-video", ProviderKind.VIDEO)
        self.router = ModelRouter([self.provider])
        self.trace_id = new_id("trace")
        self.job_manager: JobManager | None = None
        self.human_gates = HumanGateManager(self.event_bus, self.trace_id)
        self.frame_planner = RuleBasedFramePlanner()
        self.strategy_planner = GenerationStrategyPlanner()
        self.prompt_compiler = GenericPromptCompiler()
        self.technical_qc = TechnicalQC()
        self.visual_critic = MockVisualSemanticCritic({"shot_002"})
        self.cinematic_critic = MockCinematicCritic()
        self.repair_planner = RepairPlanner()
        self.evaluations: list[Evaluation] = []
        self.repair_plans: list[RepairPlan] = []
        self._restored_jobs: dict[str, GenerationJob] = {}
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
        """Validate chain boundaries and materialize explicit first/last frame plans."""

        shots_by_id = {shot.shot_id: shot for shot in project.shots}
        planned_shots: dict[str, Shot] = {}
        frame_ids: list[str] = []
        for chain in project.continuity_chains:
            boundary_state = chain.initial_state.model_copy(deep=True)
            previous_shot: Shot | None = None
            for shot_id in chain.shot_ids:
                shot = shots_by_id[shot_id]
                boundary_state = derive_next_state(boundary_state, shot)
                anchors = self.frame_planner.plan(shot, previous_shot, boundary_state)

                first_parents = [anchors.first_frame.source_artifact_id]
                first_parents = [item for item in first_parents if item]
                first = self._artifact(
                    project,
                    ArtifactType.FRAME,
                    {
                        "shot_id": shot.shot_id,
                        "boundary": "first",
                        "anchor": anchors.first_frame.model_dump(mode="json"),
                    },
                    artifact_id=f"frame_{shot.shot_id}_first",
                    role="Director",
                    tool="mock_frame_planner",
                    parent_artifact_ids=first_parents,
                )
                if anchors.first_frame.source_artifact_id is None:
                    anchors.first_frame.source_artifact_id = first.artifact_id

                last = self._artifact(
                    project,
                    ArtifactType.FRAME,
                    {
                        "shot_id": shot.shot_id,
                        "boundary": "last",
                        "anchor": anchors.last_frame.model_dump(mode="json"),
                    },
                    artifact_id=f"frame_{shot.shot_id}_last",
                    role="Director",
                    tool="mock_frame_planner",
                    parent_artifact_ids=[first.artifact_id],
                )
                anchors.last_frame.source_artifact_id = last.artifact_id
                expected_state = shot.expected_state_after.model_copy(
                    update={"last_frame_artifact_id": last.artifact_id}, deep=True
                )
                shot = shot.model_copy(
                    update={"frame_anchors": anchors, "expected_state_after": expected_state},
                    deep=True,
                )
                boundary_state.last_frame_artifact_id = last.artifact_id
                planned_shots[shot.shot_id] = shot
                previous_shot = shot
                frame_ids.extend([first.artifact_id, last.artifact_id])

        project.shots = [planned_shots[shot.shot_id] for shot in project.shots]
        self._artifact(
            project,
            ArtifactType.SHOT_PLAN,
            [shot.frame_anchors.model_dump(mode="json") for shot in project.shots],
            artifact_id="storyboard_plan",
            role="Director",
            tool="mock_storyboard_planning",
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
        """Compile, route, schedule, and register one immutable version per shot."""

        if self.job_manager is None:
            raise RuntimeError("job manager has not been initialized")
        repair_plan_ids = repair_plan_ids or {}
        capabilities = [await self.provider.capabilities()]
        shot_ids = {shot.shot_id for shot in shots}
        jobs: list[GenerationJob] = []
        request_by_job: dict[str, ProviderRequest] = {}
        prompt_artifact_by_job: dict[str, Artifact] = {}
        shot_by_job: dict[str, Shot] = {}

        updated_shots = {shot.shot_id: shot for shot in project.shots}
        for shot in shots:
            strategy = self.strategy_planner.plan(
                shot,
                capabilities,
                continuity=shot.state_before,
            )
            prompt = self.prompt_compiler.compile(shot)
            planned_shot = shot.model_copy(update={"generation_strategy": strategy}, deep=True)
            updated_shots[shot.shot_id] = planned_shot
            next_version = len(self.artifact_store.list_versions(f"video_{shot.shot_id}")) + 1
            job_id = f"generate:{shot.shot_id}:v{next_version}"
            dependency = (
                [f"generate:{shot.previous_shot_id}:v{next_version}"]
                if shot.previous_shot_id in shot_ids
                else []
            )
            job = GenerationJob(
                job_id=job_id,
                project_id=project.project_id,
                node_id="repair_accept" if shot.shot_id in repair_plan_ids else "shot_production",
                shot_id=shot.shot_id,
                continuity_chain_id=shot.continuity_chain_id,
                task="video",
                dependencies=dependency,
                priority=10,
                resource_class=ResourceClass.MEDIUM,
                retry_budget=shot.retry_budget,
                idempotency_key=job_id,
                provenance=Provenance(
                    role="Showrunner",
                    tool="local_job_scheduler",
                    prompt_package_id=prompt.prompt_package_id,
                    generation_strategy=strategy.strategy_type.value,
                    repair_plan_ids=[repair_plan_ids[shot.shot_id]]
                    if shot.shot_id in repair_plan_ids
                    else [],
                ),
            )
            prompt_artifact = self._artifact(
                project,
                ArtifactType.TEXT,
                prompt.model_dump(mode="json"),
                artifact_id=f"prompt_{shot.shot_id}",
                role="Showrunner",
                tool="generic_prompt_compiler",
                source_job_id=job_id,
                parent_artifact_ids=self._shot_input_artifact_ids(planned_shot),
            )
            generation_request = GenerationRequest(
                job_id=job_id,
                task="video",
                strategy=strategy,
                prompt_package=prompt,
                input_artifact_ids=self._shot_input_artifact_ids(planned_shot),
                requested_output_type=ArtifactType.VIDEO.value,
                resource_class=ResourceClass.MEDIUM,
                parameters={
                    "duration_seconds": shot.duration_seconds,
                    "aspect_ratio": project.brief.aspect_ratio.value,
                    "fps": project.brief.fps,
                },
            )
            request_by_job[job_id] = ProviderRequest(
                provider_id=self.provider.provider_id,
                generation_request=generation_request,
            )
            prompt_artifact_by_job[job_id] = prompt_artifact
            shot_by_job[job_id] = planned_shot
            jobs.append(job)
        project.shots = [updated_shots[shot.shot_id] for shot in project.shots]

        width, height = self._resolution(project.brief.resolution)
        created_by_job: dict[str, Artifact] = {}

        async def operation(job: GenerationJob) -> ProviderResult:
            shot = shot_by_job[job.job_id]
            strategy = shot.generation_strategy
            if strategy is None:
                raise RuntimeError("generation strategy was not planned")
            selection = await self.router.select(
                RoutingRequest(
                    task="video",
                    quality_profile=shot.quality_profile,
                    required_capabilities=strategy.required_capabilities,
                    strategy_type=strategy.strategy_type,
                    resource_class=job.resource_class,
                )
            )
            result = await self.router.get(selection.provider_id).submit(request_by_job[job.job_id])
            if not result.success:
                return result
            plan_id = repair_plan_ids.get(shot.shot_id)
            artifact = self._artifact(
                project,
                ArtifactType.VIDEO,
                {
                    "mock_video": True,
                    "shot_id": shot.shot_id,
                    "provider_request_id": result.provider_request_id,
                },
                artifact_id=f"video_{shot.shot_id}",
                role="Showrunner",
                tool="mock_video_generation",
                provider_id=selection.provider_id,
                prompt_package_id=request_by_job[job.job_id].generation_request.prompt_package.prompt_package_id,
                source_job_id=job.job_id,
                parent_artifact_ids=[
                    prompt_artifact_by_job[job.job_id].artifact_id,
                    *self._shot_input_artifact_ids(shot),
                ],
                metadata={
                    "duration_seconds": shot.duration_seconds,
                    "width": width,
                    "height": height,
                    "mock": True,
                },
                generation_strategy=strategy.strategy_type.value,
                repair_plan_ids=[plan_id] if plan_id else [],
            )
            created_by_job[job.job_id] = artifact
            return result.model_copy(update={"artifact_ids": [artifact.artifact_id]}, deep=True)

        scheduler = LocalJobScheduler(self.job_manager, LogicalResourceScheduler(capacity=4))
        completed_jobs = await scheduler.run(jobs, operation)
        if any(job.status != JobStatus.SUCCEEDED for job in completed_jobs):
            failures = [job.failure_reason or job.job_id for job in completed_jobs if job.status != JobStatus.SUCCEEDED]
            raise RuntimeError(f"shot generation failed: {'; '.join(failures)}")
        for job in completed_jobs:
            shot = shot_by_job[job.job_id]
            project.generation_history.entries.append(
                GenerationHistoryEntry(
                    job_id=job.job_id,
                    shot_id=shot.shot_id,
                    strategy_type=shot.generation_strategy.strategy_type.value,
                    succeeded=True,
                    provider_id=self.provider.provider_id,
                    metadata={"artifact_version": created_by_job[job.job_id].version},
                )
            )
        return [created_by_job[job.job_id] for job in completed_jobs]

    async def _technical_qc(self, project: Project) -> None:
        for shot in project.shots:
            self._record_evaluation(
                project, self.technical_qc.evaluate(shot, self._latest_video(shot.shot_id))
            )

    async def _visual_semantic_critic(self, project: Project) -> None:
        for shot in project.shots:
            self._record_evaluation(
                project, self.visual_critic.evaluate(shot, self._latest_video(shot.shot_id))
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
            self._emit(
                EventType.REPAIR_STARTED,
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
                self.visual_critic.evaluate(repaired_shot, repaired),
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

        for shot in project.shots:
            latest = self._latest_video(shot.shot_id)
            if not latest.selected:
                self._select(project, latest.artifact_id, latest.version)

    async def _audio_post(self, project: Project) -> None:
        selected_videos = [self._latest_video(shot.shot_id).artifact_id for shot in project.shots]
        self._artifact(
            project,
            ArtifactType.AUDIO,
            {
                "dialogue": "mock dialogue edit",
                "ambience": "mock orbital cabin ambience",
                "music": "mock restrained synth score",
            },
            artifact_id="audio_mix",
            role="Sound/Post Director",
            tool="mock_audio_post",
            parent_artifact_ids=selected_videos,
            extension="wav.placeholder",
        )

    async def _rough_cut(self, project: Project) -> None:
        selected_videos = [self._latest_video(shot.shot_id) for shot in project.shots]
        audio = self._required_artifact("audio_mix")
        self._artifact(
            project,
            ArtifactType.TIMELINE,
            {
                "tracks": {
                    "video": [artifact.artifact_id for artifact in selected_videos],
                    "audio": [audio.artifact_id],
                },
                "duration_seconds": sum(shot.duration_seconds for shot in project.shots),
            },
            artifact_id="rough_cut",
            role="Sound/Post Director",
            tool="mock_timeline_assembly",
            parent_artifact_ids=[
                *[artifact.artifact_id for artifact in selected_videos],
                audio.artifact_id,
            ],
        )

    async def _full_film_review(self, project: Project) -> None:
        rough_cut = self._required_artifact("rough_cut")
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
        selected_videos = [self._latest_video(shot.shot_id) for shot in project.shots]
        full_review_ids = [
            evaluation.evaluation_id
            for evaluation in self.evaluations
            if evaluation.target_artifact_id == rough_cut.artifact_id
        ]
        final = self._artifact(
            project,
            ArtifactType.FINAL_FILM,
            {
                "mock_final_film": True,
                "title": project.brief.title,
                "shot_count": len(project.shots),
            },
            artifact_id="final_film",
            role="Showrunner",
            tool="mock_final_render",
            parent_artifact_ids=[
                rough_cut.artifact_id,
                *[artifact.artifact_id for artifact in selected_videos],
            ],
            evaluation_ids=full_review_ids,
            extension="mp4.placeholder",
        )
        self._select(project, final.artifact_id, final.version)

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
        """Application adapters may persist additional versioned state without altering Project."""
        return {}

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
        """Restore application-owned state; Phase 1 has no additional state."""

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
            human_reviews=self.human_gates.all(),
            event_stream=self.event_bus.events(),
            final_artifact_id=final.artifact_id if final else None,
            latest_checkpoint_id=self._latest_checkpoint_id,
        )
