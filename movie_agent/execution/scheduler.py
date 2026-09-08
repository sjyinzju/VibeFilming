"""Logical resource scheduler with DAG and continuity-chain constraints."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager

from movie_agent.domain import GenerationJob, JobStatus, ResourceClass
from movie_agent.execution.jobs import JobManager, JobOperation, LocalJobExecutor

RESOURCE_WEIGHTS = {
    ResourceClass.LIGHT: 1,
    ResourceClass.MEDIUM: 2,
    ResourceClass.HEAVY: 4,
}


class ResourceScheduler(ABC):
    """Interface for acquiring and releasing logical production resources."""

    @abstractmethod
    async def acquire(self, resource_class: ResourceClass) -> None: ...

    @abstractmethod
    async def release(self, resource_class: ResourceClass) -> None: ...


class LogicalResourceScheduler(ResourceScheduler):
    """In-process weighted capacity simulation with exclusive jobs."""

    def __init__(self, capacity: int = 4) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._used = 0
        self._exclusive = False
        self._condition = asyncio.Condition()

    async def acquire(self, resource_class: ResourceClass) -> None:
        async with self._condition:
            if resource_class == ResourceClass.EXCLUSIVE:
                await self._condition.wait_for(lambda: self._used == 0 and not self._exclusive)
                self._exclusive = True
                self._used = self.capacity
                return
            weight = RESOURCE_WEIGHTS[resource_class]
            if weight > self.capacity:
                raise ValueError("resource class exceeds scheduler capacity")
            await self._condition.wait_for(
                lambda: not self._exclusive and self._used + weight <= self.capacity
            )
            self._used += weight

    async def release(self, resource_class: ResourceClass) -> None:
        async with self._condition:
            if resource_class == ResourceClass.EXCLUSIVE:
                self._exclusive = False
                self._used = 0
            else:
                self._used -= RESOURCE_WEIGHTS[resource_class]
            self._condition.notify_all()

    @asynccontextmanager
    async def lease(self, resource_class: ResourceClass):
        await self.acquire(resource_class)
        try:
            yield
        finally:
            await self.release(resource_class)


class LocalJobScheduler:
    """Run dependency-ready jobs, serializing each continuity chain."""

    def __init__(self, manager: JobManager, resources: LogicalResourceScheduler) -> None:
        self.manager = manager
        self.resources = resources
        self.executor = LocalJobExecutor(manager)
        self._chain_locks: dict[str, asyncio.Lock] = {}

    async def run(self, jobs: list[GenerationJob], operation: JobOperation) -> list[GenerationJob]:
        if self.manager.runtime_coordinator:
            return await self._run_resource_aware(jobs, operation)
        ordered = sorted(jobs, key=lambda job: (-job.priority, job.created_at))
        for job in ordered:
            if job.job_id not in {existing.job_id for existing in self.manager.all()}:
                self.manager.add(job)
        done = {job.job_id: asyncio.Event() for job in ordered}

        async def run_one(job: GenerationJob) -> None:
            try:
                for dependency_id in job.dependencies:
                    if dependency_id not in done:
                        self.manager.transition(job.job_id, JobStatus.QUEUED)
                        self.manager.transition(job.job_id, JobStatus.BLOCKED)
                        self.manager.transition(
                            job.job_id,
                            JobStatus.FAILED,
                            failure_reason=f"unknown dependency {dependency_id}",
                        )
                        return
                    await done[dependency_id].wait()
                    dependency = self.manager.get(dependency_id)
                    if dependency.status != JobStatus.SUCCEEDED:
                        self.manager.transition(job.job_id, JobStatus.QUEUED)
                        self.manager.transition(job.job_id, JobStatus.BLOCKED)
                        self.manager.transition(
                            job.job_id,
                            JobStatus.FAILED,
                            failure_reason=f"dependency {dependency_id} did not succeed",
                        )
                        return

                chain_key = job.continuity_chain_id or f"independent:{job.job_id}"
                lock = self._chain_locks.setdefault(chain_key, asyncio.Lock())
                async with lock:
                    async with self.resources.lease(job.resource_class):
                        await self.executor.execute(job.job_id, operation)
            finally:
                done[job.job_id].set()

        await asyncio.gather(*(run_one(job) for job in ordered))
        return [self.manager.get(job.job_id) for job in ordered]

    async def _run_resource_aware(self, jobs, operation):
        coordinator = self.manager.runtime_coordinator
        for job in jobs:
            if job.job_id not in self.manager._jobs:
                self.manager.add(job)
        pending = {j.job_id for j in jobs if self.manager.get(j.job_id).status in {
            JobStatus.PENDING, JobStatus.QUEUED, JobStatus.WAITING_RESOURCE}}
        waiting = set()
        while pending:
            ready = [self.manager.get(jid) for jid in pending if jid not in waiting and all(
                dependency in self.manager._jobs and self.manager.get(dependency).status == JobStatus.SUCCEEDED
                for dependency in self.manager.get(jid).dependencies)]
            # WAITING_HUMAN / BLOCKED jobs are never candidates. No dependency gets relaxed.
            if not ready:
                break
            selected = coordinator.order_ready(ready)[0]
            chain = selected.continuity_chain_id or selected.job_id
            async with self._chain_locks.setdefault(chain, asyncio.Lock()):
                result = await self.executor.execute(selected.job_id, operation)
            if result.status == JobStatus.WAITING_RESOURCE:
                waiting.add(selected.job_id)
            else:
                pending.remove(selected.job_id)
                waiting.clear()
        coordinator.set_ready_jobs([])
        return [self.manager.get(job.job_id) for job in jobs]
