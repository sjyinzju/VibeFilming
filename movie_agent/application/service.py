"""Project command service and snapshot projection over the core workflow."""

import asyncio
from collections.abc import Callable
from movie_agent.domain import Project, ProjectBrief, EventType, ReviewStatus, WorkflowNodeStatus, JobStatus
from movie_agent.orchestration import build_production_graph
from movie_agent.services.reasoning_production import ReasoningMovieProduction
from .repository import ProjectRecord, ProjectRepository, ProductionStatus as Status
from .creative_inputs import CreativeHints, CreativeInputContextBuilder


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

    def create(self, brief: ProjectBrief, creative_hints: CreativeHints | None = None) -> ProjectRecord:
        record = ProjectRecord(project=Project(brief=brief), creative_hints=creative_hints or CreativeHints())
        self.repository.save(record)
        engine = self.factory(record.project.project_id)
        engine.runner.context_builder = CreativeInputContextBuilder(record.creative_hints)
        graph = build_production_graph(record.project.project_id, engine.event_bus, engine.trace_id)
        engine.current_project, engine.current_production = record.project, graph
        engine._emit(EventType.PROJECT_CREATED, record.project.project_id, {"title": brief.title})
        engine._save_checkpoint(record.project, graph)
        self.engines[record.project.project_id] = engine
        return record

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
            record.failure_code = type(error).__name__
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
