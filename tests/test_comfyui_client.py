"""HTTP contract tests for the independently testable ComfyUI client."""

from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
import pytest
from fastapi import FastAPI, File, Form, Response, UploadFile

from movie_agent.comfyui import ComfyUIClient, ComfyUIPollingExecutionEventAdapter
from movie_agent.domain import JobStatus, ProviderErrorType
from movie_agent.providers import ProviderFailure


def test_client_reads_health_and_node_schema_from_local_api_contract() -> None:
    async def scenario() -> None:
        app = FastAPI()

        @app.get("/system_stats")
        async def system_stats():
            return {
                "system": {"comfyui_version": "0.3.fixture", "python_version": "3.12"},
                "devices": [{"name": "fixture-device", "type": "cuda"}],
            }

        @app.get("/object_info")
        async def object_info():
            return {
                "MovieAgentVideoFixture": {
                    "input": {"required": {"prompt": ["STRING", {}]}},
                    "output_node": False,
                }
            }

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://comfyui"
        ) as http:
            client = ComfyUIClient(endpoint="http://comfyui", http_client=http)
            assert await client.health()
            assert (await client.system_stats())["system"]["comfyui_version"] == "0.3.fixture"
            assert "MovieAgentVideoFixture" in await client.object_info()

    asyncio.run(scenario())


def test_polling_execution_adapter_maps_remote_states_without_fake_percent() -> None:
    async def scenario() -> None:
        app = FastAPI()
        remote_states = iter(["pending", "in_progress", "completed"])

        @app.post("/prompt")
        async def prompt(body: dict):
            return {"prompt_id": "prompt_states", "number": 1, "node_errors": {}}

        @app.get("/api/jobs/{job_id}")
        async def job(job_id: str):
            return {"id": job_id, "status": next(remote_states)}

        updates = []
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://comfyui"
        ) as http:
            client = ComfyUIClient(endpoint="http://comfyui", http_client=http)
            submission = await client.execute_prompt(
                {"10": {"class_type": "Fixture", "inputs": {}}},
                execution_adapter=ComfyUIPollingExecutionEventAdapter(poll_interval=0),
                on_update=updates.append,
            )

        assert submission.prompt_id == "prompt_states"
        assert [item.status for item in updates] == [
            JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.SUCCEEDED,
        ]
        assert all(item.progress is None and not item.progress_is_determinate for item in updates)

    asyncio.run(scenario())


def test_client_roundtrips_input_prompt_history_output_and_job_cancel() -> None:
    async def scenario() -> None:
        app = FastAPI()
        image_bytes = b"\x89PNG\r\n\x1a\nfixture"
        output_bytes = b"fixture-mp4"
        observed: dict[str, object] = {}

        @app.post("/upload/image")
        async def upload_image(
            image: UploadFile = File(...),
            type: str = Form(...),
            subfolder: str = Form(""),
            overwrite: str = Form("false"),
        ):
            content = await image.read()
            observed["upload"] = {
                "sha256": hashlib.sha256(content).hexdigest(),
                "type": type,
                "subfolder": subfolder,
                "overwrite": overwrite,
            }
            return {"name": image.filename, "subfolder": subfolder, "type": type}

        @app.post("/prompt")
        async def prompt(body: dict):
            observed["prompt"] = body
            return {"prompt_id": "prompt_fixture", "number": 7, "node_errors": {}}

        @app.get("/history/{prompt_id}")
        async def history(prompt_id: str):
            return {
                prompt_id: {
                    "outputs": {
                        "20": {"videos": [{"filename": "clip.mp4", "subfolder": "movie-agent", "type": "output"}]}
                    },
                    "status": {"status_str": "success", "completed": True, "messages": []},
                }
            }

        @app.get("/view")
        async def view(filename: str, subfolder: str = "", type: str = "output"):
            observed["view"] = (filename, subfolder, type)
            return Response(content=output_bytes, media_type="video/mp4")

        @app.get("/api/jobs/{job_id}")
        async def job(job_id: str):
            return {"id": job_id, "status": "completed"}

        @app.post("/api/jobs/{job_id}/cancel")
        async def cancel(job_id: str):
            observed["cancel"] = job_id
            return {"cancelled": True}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://comfyui"
        ) as http:
            client = ComfyUIClient(endpoint="http://comfyui", http_client=http)
            uploaded = await client.upload_image(
                filename="first_v3.png", content=image_bytes, mime_type="image/png",
                subfolder="movie-agent",
            )
            submitted = await client.submit_prompt(
                {"10": {"class_type": "MovieAgentVideoFixture", "inputs": {"first_frame": uploaded.input_name}}},
                client_id="client_fixture",
            )
            history_item = await client.history(submitted.prompt_id)
            remote_file = client.output_files(history_item, node_id="20", history_key="videos")[0]
            retrieved = await client.view(remote_file)
            job = await client.job(submitted.prompt_id)
            cancelled = await client.cancel_job(submitted.prompt_id)

        assert observed["upload"] == {
            "sha256": hashlib.sha256(image_bytes).hexdigest(),
            "type": "input", "subfolder": "movie-agent", "overwrite": "false",
        }
        assert observed["prompt"] == {
            "prompt": {"10": {"class_type": "MovieAgentVideoFixture", "inputs": {"first_frame": "movie-agent/first_v3.png"}}},
            "client_id": "client_fixture",
        }
        assert retrieved == output_bytes and observed["view"] == ("clip.mp4", "movie-agent", "output")
        assert job["status"] == "completed" and cancelled and observed["cancel"] == "prompt_fixture"

    asyncio.run(scenario())


def test_client_supports_legacy_queue_deletion_and_global_interrupt() -> None:
    async def scenario() -> None:
        app = FastAPI()
        observed: list[tuple[str, object]] = []

        @app.get("/queue")
        async def queue():
            return {"queue_running": [], "queue_pending": [[1, "prompt_pending", {}, {}, []]]}

        @app.post("/queue")
        async def delete_queue(body: dict):
            observed.append(("queue", body))
            return Response(status_code=200)

        @app.post("/interrupt")
        async def interrupt():
            observed.append(("interrupt", None))
            return Response(status_code=200)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://comfyui"
        ) as http:
            client = ComfyUIClient(endpoint="http://comfyui", http_client=http)
            queue = await client.queue()
            await client.delete_queued(["prompt_pending"])
            await client.interrupt()

        assert queue["queue_pending"][0][1] == "prompt_pending"
        assert observed == [("queue", {"delete": ["prompt_pending"]}), ("interrupt", None)]

    asyncio.run(scenario())


def test_default_websocket_adapter_maps_live_comfyui_event_frames() -> None:
    async def scenario() -> None:
        from websockets.asyncio.server import serve

        websocket_paths: list[str] = []

        async def websocket_handler(websocket):
            websocket_paths.append(websocket.request.path)
            for event in (
                {"type": "execution_start", "data": {"prompt_id": "prompt_ws"}},
                {"type": "progress_state", "data": {
                    "prompt_id": "prompt_ws",
                    "nodes": {"30": {"value": 3, "max": 10, "state": "running", "node_id": "30"}},
                }},
                {"type": "execution_success", "data": {"prompt_id": "prompt_ws"}},
            ):
                await websocket.send(json.dumps(event))

        async def prompt_transport(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/prompt"
            return httpx.Response(200, json={
                "prompt_id": "prompt_ws", "number": 1, "node_errors": {},
            })

        async with serve(websocket_handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(prompt_transport),
                base_url=f"http://127.0.0.1:{port}",
            ) as http:
                updates = []
                client = ComfyUIClient(
                    endpoint=f"http://127.0.0.1:{port}", http_client=http,
                    websocket_timeout=2,
                )
                submission = await client.execute_prompt(
                    {"10": {"class_type": "Fixture", "inputs": {}}},
                    on_update=updates.append,
                )

        assert submission.prompt_id == "prompt_ws"
        assert websocket_paths[0].startswith("/ws?clientId=")
        assert [item.status for item in updates] == [
            JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RUNNING, JobStatus.SUCCEEDED,
        ]
        assert updates[2].progress == 0.3 and updates[2].progress_is_determinate
        assert updates[2].node_id == "30"

    asyncio.run(scenario())


def test_websocket_close_after_submission_is_non_retryable_remote_uncertainty() -> None:
    async def scenario() -> None:
        from websockets.asyncio.server import serve

        async def websocket_handler(websocket):
            await websocket.close()

        async def prompt_transport(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/prompt"
            return httpx.Response(200, json={
                "prompt_id": "prompt_uncertain", "number": 1, "node_errors": {},
            })

        async with serve(websocket_handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(prompt_transport),
                base_url=f"http://127.0.0.1:{port}",
            ) as http:
                client = ComfyUIClient(
                    endpoint=f"http://127.0.0.1:{port}", http_client=http,
                    websocket_timeout=2,
                )
                with pytest.raises(ProviderFailure) as captured:
                    await client.execute_prompt({
                        "10": {"class_type": "Fixture", "inputs": {}}
                    })

        assert captured.value.error_type == ProviderErrorType.REMOTE_COMPLETION_UNCERTAIN
        assert not captured.value.retryable

    asyncio.run(scenario())
