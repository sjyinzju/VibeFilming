"""REST commands, human gates and SSE replay including application restart."""

import asyncio
import httpx
from movie_agent.api.app import create_app
from movie_agent.application.service import ProductionService
from movie_agent.storage.projects import LocalProjectRepository
from movie_agent.execution.durable_events import DurableLocalEventBus
from movie_agent.services.reasoning_production import ReasoningMovieProduction
from tests.p2a_fakes import FakeReasoningProvider
from tests.test_p2a_runtime import brief
from movie_agent.domain import EventEnvelope, EventType


def service_at(root, provider, **kwargs):
    return ProductionService(LocalProjectRepository(root), lambda pid: ReasoningMovieProduction(
        root / pid, provider, event_bus=DurableLocalEventBus(root / pid / "events")), **kwargs)


def test_rest_sse_approval_restart(tmp_path):
    async def scenario():
        provider = FakeReasoningProvider()
        service = service_at(tmp_path, provider)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service)), base_url="http://test") as client:
            assert (await client.get("/health")).status_code == 200
            record = (await client.post("/projects", json=brief().model_dump(mode="json"))).json()
            pid = record["project"]["project_id"]
            prefix = f"/projects/{pid}"
            assert (await client.post(prefix + "/start")).status_code == 202
            assert (await client.post(prefix + "/start")).status_code == 409
            await service.tasks[pid]
            assert (await client.get(prefix)).json()["status"] == "waiting_human"
            reviews = (await client.get(prefix + "/reviews")).json()
            assert (await client.post(prefix + "/resume")).status_code == 409
            rid = reviews[0]["review_id"]
            assert (await client.post(f"/reviews/{rid}/resolve", json={"approved": True})).status_code == 200
            first_events = (await client.get(prefix + "/events?follow=false")).text
            assert "event: role_output_validated" in first_events
            cursor = (await client.get(prefix + "/workflow")).json()["event_cursor"]
            assert (await client.get(prefix + "/events?follow=false", headers={"Last-Event-ID": cursor})).text == ""
            assert (await client.get(prefix + "/events?after=unknown&follow=false")).status_code == 409
        # New service and provider stand in for an actual process restart.
        resumed_provider = FakeReasoningProvider()
        resumed = service_at(tmp_path, resumed_provider)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(resumed)), base_url="http://test") as client:
            assert (await client.get(prefix + "/events?follow=false")).text == first_events
            for _ in range(3):
                assert (await client.post(prefix + "/resume")).status_code == 202
                await resumed.tasks[pid]
                state = (await client.get(prefix)).json()
                if state["status"] == "completed":
                    break
                pending = [r for r in (await client.get(prefix + "/reviews")).json() if r["status"] == "pending"]
                assert len(pending) == 1
                await client.post(f"/reviews/{pending[0]['review_id']}/resolve", json={"approved": True})
            assert state["status"] == "completed"
            assert "CreativeDirection" not in resumed_provider.calls and "StoryBible" not in resumed_provider.calls
            assert (await client.get(prefix + "/shots/scene_a-SHOT-01")).status_code == 200
            assert (await client.get("/artifacts/final_film", params={"project_id": pid})).status_code == 200
            assert (await client.get(prefix + "/shots/missing")).status_code == 404
            assert (await client.get("/projects/missing")).status_code == 404
    asyncio.run(scenario())


def test_pause_at_node_boundary_and_cancel_prevents_commit(tmp_path):
    async def scenario():
        class BlockingProvider(FakeReasoningProvider):
            def __init__(self):
                super().__init__()
                self.entered, self.release = asyncio.Event(), asyncio.Event()
            async def submit(self, request):
                if not self.calls:
                    self.entered.set()
                    await self.release.wait()
                return await super().submit(request)
        provider = BlockingProvider()
        service = service_at(tmp_path / "pause", provider, auto_approve=True)
        pid = service.create(brief()).project.project_id
        service.start(pid)
        await provider.entered.wait()
        assert service.pause(pid)["status"] == "pausing"
        provider.release.set()
        await service.tasks[pid]
        assert service.snapshot(pid)["status"] == "paused"
        assert provider.calls == ["CreativeDirection"]
        service.start(pid, resume=True)
        await service.tasks[pid]
        assert service.snapshot(pid)["status"] == "completed"
        assert provider.calls.count("CreativeDirection") == 1

        cancelled_provider = BlockingProvider()
        cancelled = service_at(tmp_path / "cancel", cancelled_provider, auto_approve=True)
        pid = cancelled.create(brief()).project.project_id
        cancelled.start(pid)
        await cancelled_provider.entered.wait()
        job = cancelled.engine(pid).job_manager.all()[0]
        result = cancelled.cancel_job(job.job_id)
        assert result.cancellation_requested
        cancelled_provider.release.set()
        await cancelled.tasks[pid]
        assert cancelled.engine(pid).current_project.creative_direction is None
        assert cancelled.engine(pid).job_manager.get(job.job_id).status.value == "cancelled"
    asyncio.run(scenario())


def test_sse_delivers_events_created_after_connection(tmp_path):
    async def scenario():
        service = service_at(tmp_path, FakeReasoningProvider())
        pid = service.create(brief()).project.project_id
        app = create_app(service)
        engine = service.engine(pid)
        cursor = engine.event_bus.events()[-1].event_id
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        await incoming.put({"type": "http.request", "body": b"", "more_body": False})
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": "GET", "scheme": "http", "server": ("test", 80),
            "client": ("test", 123), "root_path": "", "path": f"/projects/{pid}/events",
            "raw_path": f"/projects/{pid}/events".encode(), "query_string": b"",
            "headers": [(b"last-event-id", cursor.encode())]}
        task = asyncio.create_task(app(scope, incoming.get, outgoing.put))
        try:
            start = await asyncio.wait_for(outgoing.get(), 2)
            assert start["type"] == "http.response.start" and start["status"] == 200
            event = EventEnvelope(event_type=EventType.NODE_PROGRESS, project_id=pid,
                trace_id="test", node_id="brief", payload={"progress": 0.25})
            engine.event_bus.emit(event)
            body = await asyncio.wait_for(outgoing.get(), 2)
            assert event.event_id.encode() in body["body"]
            assert b"event: node_progress" in body["body"]
        finally:
            await incoming.put({"type": "http.disconnect"})
            await asyncio.wait_for(task, 2)
    asyncio.run(scenario())
