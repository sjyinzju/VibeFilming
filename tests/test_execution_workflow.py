"""DAG, job lifecycle, scheduling, checkpoint, and human-gate tests."""

import asyncio
from time import perf_counter

import pytest

from movie_agent.domain import (
    CheckpointSnapshot,
    EventType,
    GenerationJob,
    HumanGateType,
    JobStatus,
    ProviderResult,
    ResourceClass,
    WorkflowEdge,
    WorkflowNodeStatus,
)
from movie_agent.execution import (
    JobManager,
    LocalCheckpointStore,
    LocalEventBus,
    LocalJobExecutor,
    LocalJobScheduler,
    LogicalResourceScheduler,
)
from movie_agent.orchestration import HumanGateManager, build_production_graph


def make_job(job_id: str, **changes) -> GenerationJob:
    values = {
        "job_id": job_id,
        "project_id": "project_1",
        "task": "mock",
        "idempotency_key": f"idem:{job_id}",
        "resource_class": ResourceClass.LIGHT,
    }
    values.update(changes)
    return GenerationJob(**values)


def test_job_lifecycle_and_bounded_retry() -> None:
    async def scenario() -> None:
        bus = LocalEventBus()
        manager = JobManager(bus, "trace_1")
        manager.add(make_job("job_1", retry_budget=1))
        attempts = 0

        async def operation(job):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return ProviderResult(
                    provider_request_id="request_1",
                    success=False,
                    retryable=True,
                    error_message="temporary",
                )
            return ProviderResult(
                provider_request_id="request_2",
                success=True,
                artifact_ids=["artifact_1"],
            )

        result = await LocalJobExecutor(manager).execute("job_1", operation)
        assert result.status == JobStatus.SUCCEEDED
        assert result.retry_count == 1
        assert result.related_artifact_ids == ["artifact_1"]
        assert attempts == 2
        assert len(bus.events(EventType.JOB_STARTED)) == 2
        assert len(bus.events(EventType.JOB_COMPLETED)) == 1

    asyncio.run(scenario())


def test_retry_budget_stops_failure_and_cancellation_is_terminal() -> None:
    async def scenario() -> None:
        manager = JobManager(LocalEventBus(), "trace_1")
        manager.add(make_job("job_fail", retry_budget=0))

        async def fail(job):
            return ProviderResult(
                provider_request_id="request_fail",
                success=False,
                retryable=True,
                error_message="still unavailable",
            )

        failed = await LocalJobExecutor(manager).execute("job_fail", fail)
        assert failed.status == JobStatus.FAILED
        assert failed.retry_count == 0

        manager.add(make_job("job_cancel"))
        cancelled = manager.request_cancel("job_cancel")
        assert cancelled.status == JobStatus.CANCELLED
        with pytest.raises(ValueError, match="invalid job transition"):
            manager.transition("job_cancel", JobStatus.QUEUED)

    asyncio.run(scenario())


def test_scheduler_runs_independent_chains_concurrently_and_same_chain_serially() -> None:
    async def scenario() -> None:
        manager = JobManager(LocalEventBus(), "trace_1")
        scheduler = LocalJobScheduler(manager, LogicalResourceScheduler(capacity=3))
        jobs = [
            make_job("chain_a_1", continuity_chain_id="chain_a"),
            make_job("chain_a_2", continuity_chain_id="chain_a"),
            make_job("chain_b_1", continuity_chain_id="chain_b"),
        ]
        intervals: dict[str, tuple[float, float]] = {}

        async def operation(job):
            start = perf_counter()
            await asyncio.sleep(0.04)
            end = perf_counter()
            intervals[job.job_id] = (start, end)
            return ProviderResult(provider_request_id=f"req:{job.job_id}", success=True)

        results = await scheduler.run(jobs, operation)
        assert all(job.status == JobStatus.SUCCEEDED for job in results)
        first = intervals["chain_a_1"]
        second = intervals["chain_a_2"]
        independent = intervals["chain_b_1"]
        assert first[1] <= second[0] or second[1] <= first[0]
        assert any(
            start < independent[1] and independent[0] < end
            for start, end in (first, second)
        )

    asyncio.run(scenario())


def test_scheduler_honors_job_dependency() -> None:
    async def scenario() -> None:
        manager = JobManager(LocalEventBus(), "trace_1")
        scheduler = LocalJobScheduler(manager, LogicalResourceScheduler(capacity=2))
        starts: dict[str, float] = {}
        ends: dict[str, float] = {}

        async def operation(job):
            starts[job.job_id] = perf_counter()
            await asyncio.sleep(0.01)
            ends[job.job_id] = perf_counter()
            return ProviderResult(provider_request_id=f"req:{job.job_id}", success=True)

        await scheduler.run(
            [make_job("first"), make_job("second", dependencies=["first"])],
            operation,
        )
        assert starts["second"] >= ends["first"]

    asyncio.run(scenario())


def test_production_dag_dependencies_and_cycle_rejection() -> None:
    bus = LocalEventBus()
    production = build_production_graph("project_1", bus, "trace_1")
    original_edges = len(production.graph.edges)

    assert [node.node_id for node in production.ready_nodes()] == ["brief"]
    production.set_status("brief", WorkflowNodeStatus.RUNNING)
    production.set_status("brief", WorkflowNodeStatus.SUCCEEDED)
    assert [node.node_id for node in production.ready_nodes()] == ["creative_expansion"]
    assert len(bus.events(EventType.NODE_CREATED)) == len(production.graph.nodes)

    with pytest.raises(ValueError, match="acyclic"):
        production.add_edge(
            WorkflowEdge(source_node_id="final_render", target_node_id="brief")
        )
    assert len(production.graph.edges) == original_edges


def test_checkpoint_round_trip_and_resume_requeues_interrupted_jobs(tmp_path) -> None:
    store = LocalCheckpointStore(tmp_path / "checkpoints")
    job = make_job("job_running", status=JobStatus.RUNNING, retry_count=1, retry_budget=2)
    snapshot = CheckpointSnapshot(
        checkpoint_id="checkpoint_1",
        project_id="project_1",
        workflow_graph={"graph_id": "graph_1"},
        project_state={"title": "Film"},
        completed_node_ids=["brief"],
        active_jobs=[job],
        retry_state={job.job_id: 1},
    )

    store.save(snapshot)
    restored = store.latest("project_1")
    resumed = store.prepare_resume(restored)

    assert restored == snapshot
    assert resumed.active_jobs[0].status == JobStatus.QUEUED
    assert resumed.active_jobs[0].retry_count == 1
    assert resumed.completed_node_ids == ["brief"]


def test_human_gate_pause_and_resume_events() -> None:
    bus = LocalEventBus()
    gates = HumanGateManager(bus, "trace_1")
    review = gates.request(
        "project_1",
        "story_gate",
        HumanGateType.STORY_APPROVAL,
        "Approve the story?",
    )
    resolved = gates.resolve(review.review_id, approved=True, notes="Looks good")

    assert resolved.status.value == "approved"
    assert resolved.resolved_at is not None
    assert len(bus.events(EventType.HUMAN_REVIEW_REQUESTED)) == 1
    assert len(bus.events(EventType.HUMAN_REVIEW_RESOLVED)) == 1
