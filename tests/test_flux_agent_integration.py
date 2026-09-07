"""Explicit opt-in: real Qwen -> real FLUX -> registered PNG -> API preview -> resume.

MOVIE_AGENT_RUN_FLUX_INTEGRATION=1 MOVIE_AGENT_IMAGE_PROVIDER=flux_direct
Use MOVIE_AGENT_FLUX_ACCEPTANCE_WORKSPACE to select a dedicated Studio repository.
Each run creates a new project and keeps all evidence under that project's directory.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("MOVIE_AGENT_RUN_FLUX_INTEGRATION") != "1",
                    reason="opt in with MOVIE_AGENT_RUN_FLUX_INTEGRATION=1; generates real images")
def test_real_qwen_flux_agent_and_preview():
    from movie_agent.api.app import create_app
    from movie_agent.application.service import ProductionService
    from movie_agent.config import LLMConfig
    from movie_agent.domain import ProjectBrief
    from movie_agent.execution.durable_events import DurableLocalEventBus
    from movie_agent.providers.openai_compatible import OpenAICompatibleLLMProvider
    from movie_agent.providers.registry import MediaProviderSettings
    from movie_agent.services.reasoning_production import ReasoningMovieProduction
    from movie_agent.storage.projects import LocalProjectRepository

    async def run():
        settings = MediaProviderSettings.from_env()
        assert settings.image_provider == "flux_direct", "Real acceptance must explicitly select flux_direct"
        root = Path(os.environ.get("MOVIE_AGENT_FLUX_ACCEPTANCE_WORKSPACE", "workspace/flux-agent-acceptance")).resolve()
        llm = OpenAICompatibleLLMProvider(LLMConfig.from_env())
        def factory(pid):
            engine = ReasoningMovieProduction(root / pid, llm, media_settings=settings,
                event_bus=DurableLocalEventBus(root / pid / "events"))
            engine.event_bus.subscribe(lambda event: print(json.dumps({"event": event.event_type.value,
                "node": event.node_id, "job": event.job_id}), flush=True)
                if event.event_type.value in {"node_started", "node_completed", "role_output_validated",
                    "media_job_started", "media_job_completed", "media_job_failed"} else None)
            return engine
        service = ProductionService(LocalProjectRepository(root), factory, auto_approve=True)
        try:
            assert await llm.health(), "Configured Qwen endpoint is not healthy"
            brief = ProjectBrief(title="The Quiet Teapot — FLUX Agent Acceptance",
                logline="Lin finds a moment of calm beside a blue ceramic teapot in morning light.",
                story_description="One silent six-second shot in one kitchen. Lin, an adult woman in a cream linen shirt, "
                    "sits at a wooden table beside a blue ceramic teapot. She slowly rests her hand beside the teapot "
                    "and smiles softly. The emotional change is from tension to calm. No cuts, no flashbacks, no other locations.",
                target_duration=6, max_shots=1, desired_character_count=1, dialogue_density=0,
                character_descriptions=["Lin: adult woman, dark hair, cream linen shirt, quiet natural expression"],
                locations=["A sunlit kitchen with a wooden table and window"], genre=["quiet slice of life"],
                visual_style="photorealistic cinematic still, natural morning window light, tactile blue ceramic",
                resolution="1920x1080", aspect_ratio="16:9", quality_level="standard",
                preferred_camera_motion=["static"], preferred_lenses=[50],
                user_constraints=["Exactly one scene and one six-second shot.", "Only Lin is visible.",
                                  "No dialogue, no subtitles, no written text, no cuts."],
                must_preserve=["Blue ceramic teapot on a wooden table beside a window."])
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service)), base_url="http://acceptance") as client:
                created = await client.post("/projects", json=brief.model_dump(mode="json"))
                assert created.status_code == 201, created.text
                pid = created.json()["project"]["project_id"]
                print("REAL_ACCEPTANCE_PROJECT=" + pid, flush=True)
                assert (await client.post(f"/projects/{pid}/start")).status_code == 202
                await service.tasks[pid]
                snapshot = (await client.get(f"/projects/{pid}/studio")).json()
                frames = [item for item in snapshot["artifacts"] if item["artifact_type"] == "frame"]
                assert len(frames) >= 2, f"Image stage failed: {service.repository.get(pid).status}"
                assert "mock-image" not in snapshot["media_provider_ids"] and snapshot["media_mode"] == "mixed"
                evidence = []
                for frame in frames:
                    assert frame["provenance"]["provider_id"] == "flux_direct" and not frame["metadata"]["mock"]
                    preview = next(item for item in snapshot["media_previews"] if item["artifact_id"] == frame["artifact_id"])
                    response = await client.get(preview["preview_url"])
                    assert response.status_code == 200 and response.headers["content-type"].startswith("image/png")
                    digest = hashlib.sha256(response.content).hexdigest()
                    assert digest == frame["provenance"]["parameters"]["sha256"]
                    evidence.append({"artifact_id": frame["artifact_id"], "uri": frame["uri"], "sha256": digest,
                                     "preview_url": preview["preview_url"], "provenance": frame["provenance"]})
                assert snapshot["roles"] and all(item["committed"] for item in snapshot["roles"])
                # Reload the real checkpoint into a fresh engine. Completed nodes must
                # not submit images again; immutable bytes and artifact versions persist.
                fresh = factory(pid)
                resumed = await fresh.run(resume=True)
                assert resumed.completed
                for item in evidence:
                    artifact = fresh.artifact_store.get(item["artifact_id"])
                    assert artifact.uri == item["uri"]
                    with fresh.binary_store.open(artifact.uri) as stream:
                        assert hashlib.sha256(stream.read()).hexdigest() == item["sha256"]
                record = {"project_id": pid, "workflow_completed": resumed.completed,
                    "reasoning_provider": llm.provider_id, "media_provider_ids": snapshot["media_provider_ids"],
                    "roles": [item["invocation"]["role_id"] for item in snapshot["roles"]],
                    "api_preview_verified": True, "checkpoint_resume_verified": True, "frames": evidence}
                (root / pid / "flux_acceptance.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
                print("REAL_ACCEPTANCE_RECORD=" + str(root / pid / "flux_acceptance.json"), flush=True)
        finally:
            await service.shutdown()
            await llm.aclose()
    asyncio.run(run())
