"""Strict job lifecycle and bounded retry executor."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from movie_agent.domain import (
    EventEnvelope,
    EventType,
    GenerationJob,
    JobStatus,
    ProviderResult,
    utc_now,
)
from movie_agent.execution.events import EventBus

ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.PENDING: {JobStatus.QUEUED, JobStatus.CANCELLED},
    JobStatus.QUEUED: {JobStatus.BLOCKED, JobStatus.WAITING_RESOURCE, JobStatus.PREPARING,
                       JobStatus.PREPARING_MODEL, JobStatus.CANCELLED},
    JobStatus.BLOCKED: {JobStatus.QUEUED, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.WAITING_RESOURCE: {JobStatus.QUEUED, JobStatus.PREPARING, JobStatus.CANCELLED},
    JobStatus.PREPARING: {JobStatus.PREPARING_MODEL, JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.PREPARING_MODEL: {JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.RUNNING: {
        JobStatus.PREPARING,
        JobStatus.EVALUATING,
        JobStatus.UPLOADING,
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.UPLOADING: {JobStatus.EVALUATING, JobStatus.SUCCEEDED, JobStatus.FAILED,
                          JobStatus.CANCELLED},
    JobStatus.EVALUATING: {
        JobStatus.REPAIRING,
        JobStatus.WAITING_HUMAN,
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.REPAIRING: {
        JobStatus.PREPARING,
        JobStatus.EVALUATING,
        JobStatus.WAITING_HUMAN,
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.WAITING_HUMAN: {
        JobStatus.PREPARING,
        JobStatus.REPAIRING,
        JobStatus.EVALUATING,
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.SUCCEEDED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
}


class JobManager:
    """In-memory job repository enforcing lifecycle invariants and events."""

    def __init__(self, event_bus: EventBus, trace_id: str) -> None:
        self.event_bus = event_bus
        self.trace_id = trace_id
        self._jobs: dict[str, GenerationJob] = {}
        self._idempotency: dict[str, str] = {}

    def add(self, job: GenerationJob) -> GenerationJob:
        if job.job_id in self._jobs:
            raise ValueError(f"duplicate job ID {job.job_id}")
        existing = self._idempotency.get(job.idempotency_key)
        if existing:
            return self._jobs[existing]
        self._jobs[job.job_id] = job
        self._idempotency[job.idempotency_key] = job.job_id
        self._emit(job, EventType.JOB_CREATED)
        self._emit_media(job, EventType.MEDIA_JOB_CREATED)
        return job

    def get(self, job_id: str) -> GenerationJob:
        return self._jobs[job_id]

    def all(self) -> list[GenerationJob]:
        return list(self._jobs.values())

    def transition(
        self,
        job_id: str,
        target: JobStatus,
        *,
        failure_reason: str | None = None,
    ) -> GenerationJob:
        job = self.get(job_id)
        if target not in ALLOWED_TRANSITIONS[job.status]:
            raise ValueError(f"invalid job transition {job.status.value} -> {target.value}")
        now = utc_now()
        changes: dict[str, object] = {"status": target}
        if target == JobStatus.QUEUED:
            changes["queued_at"] = now
        elif target == JobStatus.RUNNING:
            changes["started_at"] = job.started_at or now
        elif target in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
            changes["completed_at"] = now
            if target == JobStatus.SUCCEEDED:
                changes["progress"] = 1.0
        if failure_reason is not None:
            changes["failure_reason"] = failure_reason
        updated = job.model_copy(update=changes)
        self._jobs[job_id] = updated
        event_type = {
            JobStatus.RUNNING: EventType.JOB_STARTED,
            JobStatus.SUCCEEDED: EventType.JOB_COMPLETED,
            JobStatus.FAILED: EventType.JOB_FAILED,
        }.get(target)
        if event_type:
            self._emit(updated, event_type)
        media_event = {
            JobStatus.RUNNING: EventType.MEDIA_JOB_STARTED,
            JobStatus.SUCCEEDED: EventType.MEDIA_JOB_COMPLETED,
            JobStatus.FAILED: EventType.MEDIA_JOB_FAILED,
        }.get(target)
        if media_event and self._is_media(updated):
            self._emit_media(updated, media_event)
        return updated

    def progress(self, job_id: str, value: float) -> GenerationJob:
        job = self.get(job_id)
        updated = job.model_copy(update={"progress": value})
        self._jobs[job_id] = updated
        self._emit(updated, EventType.JOB_PROGRESS, {"progress": value})
        if self._is_media(updated):
            self._emit_media(updated, EventType.MEDIA_JOB_PROGRESS, {"progress": value})
        return updated

    def request_cancel(self, job_id: str) -> GenerationJob:
        job = self.get(job_id)
        updated = job.model_copy(update={"cancellation_requested": True})
        self._jobs[job_id] = updated
        if updated.status in {
            JobStatus.PENDING,
            JobStatus.QUEUED,
            JobStatus.BLOCKED,
            JobStatus.WAITING_RESOURCE,
            JobStatus.PREPARING,
            JobStatus.PREPARING_MODEL,
            JobStatus.WAITING_HUMAN,
        }:
            return self.transition(job_id, JobStatus.CANCELLED)
        return updated

    def increment_retry(self, job_id: str) -> GenerationJob:
        job = self.get(job_id)
        if job.retry_count >= job.retry_budget:
            raise RuntimeError("retry budget exceeded")
        updated = job.model_copy(update={"retry_count": job.retry_count + 1})
        self._jobs[job_id] = updated
        return updated

    def _emit(
        self,
        job: GenerationJob,
        event_type: EventType,
        payload: dict | None = None,
    ) -> None:
        self.event_bus.emit(
            EventEnvelope(
                event_type=event_type,
                project_id=job.project_id,
                trace_id=self.trace_id,
                node_id=job.node_id,
                job_id=job.job_id,
                payload=payload or {"status": job.status.value},
            )
        )

    @staticmethod
    def _is_media(job: GenerationJob) -> bool:
        return job.task in {"frame", "image", "video", "vision", "speech", "music",
                            "sfx", "foley", "ambience", "audio", "post"}

    def _emit_media(
        self,
        job: GenerationJob,
        event_type: EventType,
        payload: dict | None = None,
    ) -> None:
        if self._is_media(job):
            self._emit(job, event_type, payload)


JobOperation = Callable[[GenerationJob], Awaitable[ProviderResult]]


class LocalJobExecutor:
    """Execute provider work with cooperative cancellation and bounded retry."""

    def __init__(self, manager: JobManager) -> None:
        self.manager = manager

    async def execute(self, job_id: str, operation: JobOperation) -> GenerationJob:
        job = self.manager.get(job_id)
        if job.status == JobStatus.PENDING:
            job = self.manager.transition(job_id, JobStatus.QUEUED)
        if job.cancellation_requested or job.status == JobStatus.CANCELLED:
            if job.status != JobStatus.CANCELLED:
                return self.manager.transition(job_id, JobStatus.CANCELLED)
            return job
        if job.status != JobStatus.QUEUED:
            raise ValueError("executor requires a pending or queued job")

        self.manager.transition(job_id, JobStatus.PREPARING)
        job = self.manager.transition(job_id, JobStatus.RUNNING)
        while True:
            result = await operation(job)
            job = self.manager.get(job_id)
            if job.cancellation_requested:
                return self.manager.transition(job_id, JobStatus.CANCELLED)
            if result.success:
                updated = job.model_copy(
                    update={"related_artifact_ids": list(result.artifact_ids)}
                )
                self.manager._jobs[job_id] = updated
                return self.manager.transition(job_id, JobStatus.SUCCEEDED)
            if result.retryable and job.retry_count < job.retry_budget:
                job = self.manager.increment_retry(job_id)
                self.manager.transition(job_id, JobStatus.PREPARING)
                job = self.manager.transition(job_id, JobStatus.RUNNING)
                continue
            reason = result.error_message or "provider operation failed"
            return self.manager.transition(job_id, JobStatus.FAILED, failure_reason=reason)
