"""Project command service and snapshot projection over the core workflow."""

import asyncio
from collections.abc import Callable
from movie_agent.domain import Project, ProjectBrief, EventType, ReviewStatus, WorkflowNodeStatus, JobStatus
from movie_agent.orchestration import build_production_graph
from movie_agent.services.reasoning_production import ReasoningMovieProduction
from movie_agent.providers.base import ProviderFailure
from movie_agent.orchestration.runtime.runner import RoleOutputInvalid
from movie_agent.orchestration.runtime.terminal import is_terminal_only
from movie_agent.orchestration.runtime.cinematographer_drafts import ShotPlanDraft
from .repository import ProjectRecord, ProjectRepository, ProductionStatus as Status
from .creative_inputs import CreativeHints, CreativeInputContextBuilder
from movie_agent.media import ImageReferenceBindingInput, ImageReferenceUploadService, MediaReference


class CommandConflict(ValueError):
    """Command cannot be applied to the current durable production state."""


class ProductionService:
    """Single-worker application service; commands never let an LLM mutate workflow policy."""

    def __init__(self, repository: ProjectRepository,
                 factory: Callable[[str], ReasoningMovieProduction], *, auto_approve: bool = False):
        self.repository, self.factory = repository, factory
        self.auto_approve = auto_approve
        self.engines: dict[str, ReasoningMovieProduction] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        repository_root = getattr(repository, "root", None)
        if repository_root is None:
            raise ValueError("ProductionService requires a repository with a local root for draft uploads")
        self.reference_uploads = ImageReferenceUploadService(repository_root / "_draft_references")

    def create(self, brief: ProjectBrief, creative_hints: CreativeHints | None = None,
               draft_id: str | None = None) -> ProjectRecord:
        record = ProjectRecord(project=Project(brief=brief), creative_hints=creative_hints or CreativeHints())
        engine = self.factory(record.project.project_id)
        graph = build_production_graph(record.project.project_id, engine.event_bus, engine.trace_id)
        engine.current_project, engine.current_production = record.project, graph
        if draft_id:
            adopted = self.reference_uploads.adopt_draft(
                draft_id, record.project.project_id,
                artifacts=engine.artifact_store, binaries=engine.binary_store,
                bank=engine.reference_bank,
            )
            record.creative_hints.media_references = adopted
            record.project.brief.reference_images = list(dict.fromkeys([
                *record.project.brief.reference_images,
                *[item.artifact_id for item in adopted if item.binding_scope.value == "project"],
            ]))
        self.repository.save(record)
        engine.runner.context_builder = CreativeInputContextBuilder(record.creative_hints)
        engine._emit(EventType.PROJECT_CREATED, record.project.project_id, {"title": brief.title})
        engine._save_checkpoint(record.project, graph)
        self.engines[record.project.project_id] = engine
        return record

    def _ensure_reference_mutation(self, project_id: str):
        record = self.repository.get(project_id)
        if record.status in {Status.RUNNING, Status.PAUSING, Status.CANCELLED}:
            raise CommandConflict("References cannot change while production is running or cancelled")
        return self.engine(project_id)

    def add_image_reference(self, project_id: str, content: bytes, *, filename: str | None,
                            mime_type: str, binding: ImageReferenceBindingInput):
        engine = self._ensure_reference_mutation(project_id)
        project = engine.current_project
        if binding.binding_scope.value == "entity":
            entity_ids = {item.character_id for item in project.characters}
            entity_ids.update(item.location_id for item in project.locations)
            entity_ids.update(item.prop_id for item in project.props)
            if binding.entity_id not in entity_ids:
                raise CommandConflict("Reference entity does not belong to this project")
        if binding.binding_scope.value == "scene" and binding.scene_id not in {
            item.scene_id for item in project.scenes
        }:
            raise CommandConflict("Reference scene does not belong to this project")
        if binding.binding_scope.value in {"shot", "frame"} and binding.shot_id not in {
            item.shot_id for item in project.shots
        }:
            raise CommandConflict("Reference shot does not belong to this project")
        result = self.reference_uploads.upload_project(
            project_id, content, filename=filename, mime_type=mime_type, binding=binding,
            artifacts=engine.artifact_store, binaries=engine.binary_store,
            bank=engine.reference_bank,
        )
        engine._emit(EventType.ARTIFACT_CREATED, project_id,
                     {"artifact_id": result.artifact_id, "artifact_type": "image", "version": 1})
        engine._emit(EventType.REFERENCE_BOUND, project_id,
                     {"reference_id": result.reference.reference_id,
                      "artifact_id": result.artifact_id,
                      "binding_scope": result.reference.binding_scope.value})
        engine._save_checkpoint(engine.current_project, engine.current_production)
        return result

    def remove_reference(self, project_id: str, reference_id: str) -> MediaReference:
        engine = self._ensure_reference_mutation(project_id)
        reference = engine.reference_bank.unbind(reference_id)
        engine._emit(EventType.REFERENCE_UNBOUND, project_id,
                     {"reference_id": reference_id, "artifact_id": reference.artifact_id})
        engine._save_checkpoint(engine.current_project, engine.current_production)
        return reference

    def engine(self, project_id: str) -> ReasoningMovieProduction:
        record = self.repository.get(project_id)
        if project_id not in self.engines:
            engine = self.factory(project_id)
            engine.runner.context_builder = CreativeInputContextBuilder(record.creative_hints)
            project, graph = engine._restore()
            engine.current_project, engine.current_production = project, graph
            self.engines[project_id] = engine
            if record.status in {Status.RUNNING, Status.PAUSING}:
                record.status = Status.PAUSED
                self.repository.save(record)
        return self.engines[project_id]

    def snapshot(self, project_id: str):
        engine = self.engine(project_id)
        record = self.repository.get(project_id)
        events = engine.event_bus.events()
        return {"project": engine.current_project.model_dump(mode="json"),
            "status": record.status.value, "failure_code": record.failure_code,
            "event_cursor": events[-1].event_id if events else None}

    def start(self, project_id: str, *, resume: bool = False):
        engine = self.engine(project_id)
        record = self.repository.get(project_id)
        if project_id in self.tasks and not self.tasks[project_id].done():
            raise CommandConflict("Production is already running")
        if record.status in {Status.COMPLETED, Status.CANCELLED}:
            raise CommandConflict("Production is terminal")
        if not resume and record.status != Status.CREATED:
            raise CommandConflict("Use resume for an existing production")
        reviews = engine.human_gates.all()
        if any(r.status != ReviewStatus.APPROVED for r in reviews):
            raise CommandConflict("Resolve outstanding human review before resume")
        for result in engine.role_results.values():
            if result.failure_code == 'ROLE_OUTPUT_INVALID' and not result.committed:
                scene = next((s for s in engine.current_project.scenes if s.scene_id == result.invocation.scene_id), None)
                target = engine.runner.registry.target(engine.runner.registry.get(result.invocation.role_id))
                _, report = engine.runner.validator.parse(target, result.pending_output or '', engine.current_project, scene=scene)
                if not report.valid:
                    raise CommandConflict('Structured repair budget exhausted. An explicit planning revision is required.')
        engine.pause_requested = False
        record.status, record.failure_code = Status.RUNNING, None
        self.repository.save(record)
        self.tasks[project_id] = asyncio.create_task(self._execute(project_id, engine))
        return {"project_id": project_id, "status": record.status.value}

    async def _execute(self, project_id, engine):
        record = self.repository.get(project_id)
        try:
            result = await engine.run(resume=True, auto_approve=self.auto_approve)
            record.status = Status.COMPLETED if result.completed else (
                Status.WAITING_HUMAN if any(n.status == WorkflowNodeStatus.WAITING_HUMAN
                    for n in result.workflow_graph.nodes) else Status.PAUSED)
        except asyncio.CancelledError:
            if engine.current_project and engine.current_production:
                engine._save_checkpoint(engine.current_project, engine.current_production)
            record.status = Status.PAUSED
            raise
        except Exception as error:
            record.status = Status.FAILED
            # Error type only: arbitrary transport messages must not disclose secrets.
            record.failure_code = (error.error_type.value if isinstance(error, ProviderFailure) else
                                   'ROLE_OUTPUT_INVALID' if isinstance(error, RoleOutputInvalid) else type(error).__name__)
        finally:
            self.repository.save(record)

    def pause(self, project_id):
        engine = self.engine(project_id)
        record = self.repository.get(project_id)
        if record.status not in {Status.RUNNING, Status.PAUSING}:
            raise CommandConflict("Pause requires a running project")
        engine.pause_requested = True
        record.status = Status.PAUSING
        self.repository.save(record)
        return {"project_id": project_id, "status": record.status.value,
                "pause_policy": "after_current_node"}

    def terminal_revision_scene(self, project_id):
        engine = self.engine(project_id)
        if self.repository.get(project_id).status != Status.FAILED:
            return None
        candidates = [r for r in engine.role_results.values() if not r.committed and
            r.failure_code == 'ROLE_OUTPUT_INVALID' and r.invocation.role_id.value == 'cinematographer']
        if len(candidates) != 1:
            return None
        result = candidates[0]
        if result.invocation.semantic_revision or any(r.invocation.semantic_revision and
            r.invocation.scene_id == result.invocation.scene_id for r in engine.role_revision_history):
            return None
        scene = next((s for s in engine.current_project.scenes if s.scene_id == result.invocation.scene_id), None)
        if not scene:
            return None
        output, report = engine.runner.validator.parse(ShotPlanDraft, result.pending_output or '', engine.current_project, scene=scene)
        return scene.scene_id if output is not None and is_terminal_only(report) else None

    def revise_terminal(self, project_id, scene_id, authorization_reference):
        if not authorization_reference.strip():
            raise CommandConflict('An explicit authorization reference is required')
        if project_id in self.tasks and not self.tasks[project_id].done():
            raise CommandConflict('Production is already running')
        if self.terminal_revision_scene(project_id) != scene_id:
            raise CommandConflict('No eligible terminal planning revision remains')
        engine = self.engine(project_id)
        if any(r.status != ReviewStatus.APPROVED for r in engine.human_gates.all()):
            raise CommandConflict('Resolve outstanding human review before revision')
        engine.authorize_semantic_revision(scene_id, authorization_reference)
        return self.start(project_id, resume=True)

    def recover_video_job(
        self,
        project_id: str,
        job_id: str,
        remote_prompt_id: str,
        authorization_reference: str,
    ):
        if self.repository.get(project_id).status != Status.FAILED:
            raise CommandConflict("Video recovery requires a failed project")
        if project_id in self.tasks and not self.tasks[project_id].done():
            raise CommandConflict("Production is already running")
        engine = self.engine(project_id)
        try:
            engine.authorize_video_replay(job_id, remote_prompt_id, authorization_reference)
        except ValueError as error:
            raise CommandConflict(str(error)) from error
        return self.start(project_id, resume=True)

    def locate(self, kind, identity, project_id=None):
        matches = []
        for pid in [project_id] if project_id else self.repository.list_ids():
            engine = self.engine(pid)
            values = (engine.human_gates.all() if kind == "review" else
                      engine._all_jobs() if kind == "job" else engine.artifact_store.list_all())
            found = [v for v in values if getattr(v, kind + "_id") == identity]
            if found:
                matches.append((engine, found[-1]))
        if not matches:
            raise KeyError(identity)
        if len(matches) > 1:
            raise CommandConflict("ID exists in multiple projects; supply project_id")
        return matches[0]

    async def cancel(self, project_id):
        """Stop local production and retain its checkpoint/history. Remote work may finish."""
        engine = self.engine(project_id)
        record = self.repository.get(project_id)
        if record.status in {Status.COMPLETED, Status.CANCELLED}:
            raise CommandConflict("Production is terminal")
        for job in engine._all_jobs():
            if job.status not in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
                self.cancel_job(job.job_id, project_id)
        task = self.tasks.get(project_id)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # The local task has stopped: finish its cooperative cancellation records.
        if engine.job_manager:
            for job in engine.job_manager.all():
                if job.cancellation_requested and job.status not in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
                    engine.job_manager.transition(job.job_id, JobStatus.CANCELLED)
        for node in engine.current_production.graph.nodes:
            if node.status not in {WorkflowNodeStatus.SUCCEEDED, WorkflowNodeStatus.FAILED, WorkflowNodeStatus.SKIPPED}:
                engine.current_production.set_status(node.node_id, WorkflowNodeStatus.CANCELLED)
        engine._save_checkpoint(engine.current_project, engine.current_production)
        record.status = Status.CANCELLED
        self.repository.save(record)
        return {"project_id": project_id, "status": record.status}

    def resolve_review(self, review_id, approved, notes):
        engine, review = self.locate("review", review_id)
        try:
            result = engine.human_gates.resolve(review_id, approved, notes)
        except ValueError as error:
            raise CommandConflict(str(error)) from error
        engine._save_checkpoint(engine.current_project, engine.current_production)
        return result

    def cancel_job(self, job_id, project_id=None):
        engine, job = self.locate("job", job_id, project_id)
        if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
            raise CommandConflict("Job is already terminal")
        if engine.job_manager and job_id in {j.job_id for j in engine.job_manager.all()}:
            cancelled = engine.job_manager.request_cancel(job_id)
        else:
            cancelled = job.model_copy(update={"status": JobStatus.CANCELLED, "cancellation_requested": True})
            engine._restored_jobs[job_id] = cancelled
        engine._save_checkpoint(engine.current_project, engine.current_production)
        return cancelled

    async def shutdown(self):
        tasks = [t for t in self.tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
