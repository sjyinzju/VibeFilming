"""Replaceable checkpoint persistence and resume normalization."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from movie_agent.domain import CheckpointSnapshot, JobStatus


class CheckpointStore(ABC):
    """Persistence interface for production recovery snapshots."""

    @abstractmethod
    def save(self, snapshot: CheckpointSnapshot) -> CheckpointSnapshot: ...

    @abstractmethod
    def load(self, project_id: str, checkpoint_id: str) -> CheckpointSnapshot: ...

    @abstractmethod
    def latest(self, project_id: str) -> CheckpointSnapshot | None: ...


class LocalCheckpointStore(CheckpointStore):
    """Atomic JSON checkpoint store scoped by project."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, snapshot: CheckpointSnapshot) -> CheckpointSnapshot:
        project_dir = self.root / snapshot.project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        target = project_dir / f"{snapshot.checkpoint_id}.json"
        if target.exists():
            raise FileExistsError(target)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(target)
        marker = project_dir / "LATEST.tmp"
        marker.write_text(snapshot.checkpoint_id, encoding="utf-8")
        marker.replace(project_dir / "LATEST")
        return snapshot

    def load(self, project_id: str, checkpoint_id: str) -> CheckpointSnapshot:
        target = self.root / project_id / f"{checkpoint_id}.json"
        return CheckpointSnapshot.model_validate_json(target.read_text(encoding="utf-8"))

    def latest(self, project_id: str) -> CheckpointSnapshot | None:
        marker = self.root / project_id / "LATEST"
        if not marker.exists():
            return None
        return self.load(project_id, marker.read_text(encoding="utf-8").strip())

    def prepare_resume(self, snapshot: CheckpointSnapshot) -> CheckpointSnapshot:
        """Requeue interrupted active states while preserving retry counters."""

        interrupted = {
            JobStatus.PREPARING,
            JobStatus.RUNNING,
            JobStatus.EVALUATING,
            JobStatus.REPAIRING,
        }
        jobs = [
            job.model_copy(
                update={
                    "status": JobStatus.QUEUED,
                    "started_at": None,
                    "completed_at": None,
                    "failure_reason": None,
                }
            )
            if job.status in interrupted
            else job.model_copy(deep=True)
            for job in snapshot.active_jobs
        ]
        return snapshot.model_copy(update={"active_jobs": jobs}, deep=True)
