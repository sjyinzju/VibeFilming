"""Safe MIME, preview, thumbnail, and byte-range media transport."""

from __future__ import annotations

import asyncio

import httpx

from movie_agent.api.app import create_app
from tests.p2a_fakes import FakeReasoningProvider
from tests.test_p2a_api import service_at
from tests.test_p2a_runtime import brief


def test_media_serving_resolves_ids_and_supports_video_ranges(tmp_path) -> None:
    async def scenario() -> None:
        service = service_at(tmp_path, FakeReasoningProvider(), auto_approve=True)
        pid = service.create(brief()).project.project_id
        service.start(pid)
        await service.tasks[pid]
        transport = httpx.ASGITransport(app=create_app(service))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            image = await client.get(
                "/artifacts/frame_scene_a-SHOT-01_first/content", params={"project_id": pid}
            )
            assert image.status_code == 200 and image.headers["content-type"].startswith("image/png")
            assert image.content.startswith(b"\x89PNG")
            video = await client.get(
                "/artifacts/video_scene_a-SHOT-01/content",
                params={"project_id": pid}, headers={"Range": "bytes=0-31"},
            )
            assert video.status_code == 206 and len(video.content) == 32
            assert video.headers["content-range"].startswith("bytes 0-31/")
            assert video.headers["accept-ranges"] == "bytes"
            assert video.headers["content-type"].startswith("video/mp4")
            invalid = await client.get(
                "/artifacts/video_scene_a-SHOT-01/content",
                params={"project_id": pid}, headers={"Range": "bytes=999999-"},
            )
            assert invalid.status_code == 416
            thumb = await client.get(
                "/artifacts/video_scene_a-SHOT-01/thumbnail", params={"project_id": pid}
            )
            assert thumb.status_code == 200 and thumb.content.startswith(b"\x89PNG")
            audio = await client.get("/artifacts/audio_music/preview", params={"project_id": pid})
            assert audio.status_code == 200 and audio.headers["content-type"].startswith("audio/wav")
            assert (await client.get("/artifacts/missing/content", params={"project_id": pid})).status_code == 404
            providers = await client.get(f"/projects/{pid}/media/providers")
            assert providers.status_code == 200 and len(providers.json()) == 5
            selected = await client.post(
                "/artifacts/video_scene_a-SHOT-01/select",
                params={"project_id": pid, "version": 1},
            )
            assert selected.status_code == 200 and selected.json()["selected"] is True

    asyncio.run(scenario())
