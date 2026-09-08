"""One controlled P4A Spark acceptance; no planning, frame generation or film rerun."""

import argparse
import asyncio
import json
from pathlib import Path

from movie_agent.artifacts import LocalArtifactStore
from movie_agent.config import LLMConfig
from movie_agent.domain import GenerationJob, PromptPackage, ResourceClass, EventType
from movie_agent.execution import JobManager
from movie_agent.execution.durable_events import DurableLocalEventBus
from movie_agent.media import (MediaReference, ReferenceType, VideoGenerationRequest,
    VideoGenerationMode, CameraMotionSpec, VideoCapabilities)
from movie_agent.media.runtime import MediaRuntime
from movie_agent.media.storage import LocalBinaryArtifactStore
from movie_agent.media.transport import MediaReferenceBinaryResolver
from movie_agent.model_services.wiring import build_spark_runtime
from movie_agent.providers.registry import MediaProviderSettings, ProviderRegistry
from movie_agent.providers.comfyui_video import ComfyUIVideoProvider
from movie_agent.comfyui import ComfyUIClient, ComfyUIWorkflowRegistry


async def accept(root, source, *, inspect_only=False):
    root.mkdir(parents=True, exist_ok=True)
    report_file = root / "acceptance.json"
    if report_file.exists() and not inspect_only:
        return json.loads(report_file.read_text(encoding="utf-8"))
    settings = MediaProviderSettings.from_env()
    runtime = build_spark_runtime(LLMConfig.from_env(), settings)
    events = DurableLocalEventBus(root / "events")
    project_id = "p4a-resource-acceptance"
    runtime.bind(project_id, events, "p4a-real")
    def progress(event):
        if event.event_type in {EventType.MODEL_SERVICE_STATUS_CHANGED, EventType.RESOURCE_LEASE_ACQUIRED,
                EventType.RESOURCE_LEASE_RELEASED, EventType.RESOURCE_ADMISSION_WAIT}:
            print(json.dumps({"event": event.event_type.value, "service": event.payload.get("service_id"),
                              "status": event.payload.get("status"), "job": event.job_id}), flush=True)
    events.subscribe(progress)
    initial = {}
    for service in runtime.manager.all():
        initial[service.descriptor.service_id] = (await service.status()).value
    snapshot = await runtime.snapshot(project_id)
    if inspect_only:
        return {"services": initial, "snapshot": snapshot.model_dump(mode="json")}
    source_artifacts = LocalArtifactStore(source / "artifacts")
    source_binaries = LocalBinaryArtifactStore(source / "artifacts" / "media")
    references = []
    for position, kind in [("first", ReferenceType.FIRST_FRAME), ("last", ReferenceType.LAST_FRAME)]:
        artifact = source_artifacts.get(f"frame_scene_01-SHOT-01_{position}")
        if artifact is None:
            raise RuntimeError("committed acceptance frame is unavailable")
        references.append(MediaReference(reference_type=kind, artifact_id=artifact.artifact_id, version=artifact.version))
    # Exercise real lifecycle adapters without repeating Qwen reasoning or FLUX inference.
    for sid, pid in [("qwen", "reasoning"), ("flux", "flux_direct")]:
        warm = GenerationJob(job_id=f"p4a-warm:{sid}", project_id=project_id, task="warmup",
            provider_id=pid, idempotency_key=f"p4a-warm:{sid}", resource_class=ResourceClass.HEAVY)
        lease = await runtime.acquire(warm)
        if lease is None:
            raise RuntimeError("real acceptance requires enabled resource scheduling")
        await runtime.release(lease)
    artifacts = LocalArtifactStore(root / "artifacts")
    binaries = LocalBinaryArtifactStore(root / "artifacts" / "media")
    provider = ComfyUIVideoProvider(
        client=ComfyUIClient(endpoint=settings.comfyui_endpoint, timeout=1800, websocket_timeout=1800),
        workflows=ComfyUIWorkflowRegistry(), workflow_profile=settings.comfyui_workflow_profile,
        resolver=MediaReferenceBinaryResolver(source_artifacts, source_binaries),
        runtime_capabilities=VideoCapabilities(first_frame=True, last_frame=True, first_last_frame=True,
            audio_generation=True, max_duration_seconds=15, max_width=1344, max_height=1344, supported_fps=[24]))
    registry = ProviderRegistry()
    registry.register(provider)
    media = MediaRuntime(artifacts, binaries, registry, events, "p4a-real", runtime_coordinator=runtime)
    jobs = JobManager(events, "p4a-real")
    media.bind_jobs(jobs)
    request = VideoGenerationRequest(job_id="p4a:h3:minimal:v1", project_id=project_id,
        shot_id="p4a-minimal", output_artifact_id="p4a_minimal_video",
        prompt_package=PromptPackage(compiler_id="p4a-acceptance", compiler_version="1.0.0",
            positive_prompt="Warm cinematic kitchen. A small natural movement. Preserve identity and composition. Soft room tone.",
            negative_prompt="flicker, identity drift"),
        references=references, first_frame=references[0], last_frame=references[1],
        mode=VideoGenerationMode.FIRST_LAST_FRAME_TO_VIDEO, duration_seconds=22 / 24,
        fps=24, width=608, height=352, aspect_ratio="16:9", seed=20260908,
        camera_motion=CameraMotionSpec(motion_type="static"), resource_class=ResourceClass.EXCLUSIVE)
    active = GenerationJob(job_id=request.job_id, project_id=project_id, task="video",
        provider_id=provider.provider_id, shot_id=request.shot_id, idempotency_key=request.job_id,
        resource_class=ResourceClass.EXCLUSIVE, strategy_type="first_last_frame_to_video",
        input_artifact_ids=[r.artifact_id for r in references])
    try:
        video = await media.generate_video(active, request)
    finally:
        (root / "jobs.json").write_text(json.dumps([j.model_dump(mode="json") for j in jobs.all()], indent=2), encoding="utf-8")
    # Near-term affinity keeps the successfully used H3 service warm without another inference.
    next_job = active.model_copy(update={"job_id": "p4a:h3:next-ready", "idempotency_key": "p4a:h3:next-ready"})
    runtime.set_ready_jobs([next_job])
    await runtime.maintain(project_id)
    final = {}
    for service in runtime.manager.all():
        final[service.descriptor.service_id] = {"status": (await service.status()).value,
                                                "oom_killed": await service.oom_killed()}
    report = {"initial_services": initial, "initial_snapshot": snapshot.model_dump(mode="json"),
        "final_services": final, "job": jobs.get(active.job_id).model_dump(mode="json"),
        "video": video.model_dump(mode="json"), "runtime": runtime.view(),
        "next_ready_affinity": "comfyui", "retained_warm": final["comfyui"]["status"] == "ready"}
    assert not runtime.active_leases(), "completed job leaked a lease"
    assert not any(x["oom_killed"] for x in final.values()), "OOM during acceptance"
    assert any("drain/stop:qwen" in d.actions or "drain/stop:flux" in d.actions for d in runtime.decisions)
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="explicitly run one minimal real H3 inference")
    parser.add_argument("--root", type=Path, default=Path("workspace/p4a-resource-acceptance-20260908"))
    parser.add_argument("--source", type=Path, default=Path("workspace/flux-agent-acceptance-20260907/project_ba41e17ddc724deab7802abf545719fa"))
    args = parser.parse_args()
    report = asyncio.run(accept(args.root.resolve(), args.source.resolve(), inspect_only=not args.run))
    print(json.dumps({"result": "passed" if args.run else "observed", "report": str(args.root / "acceptance.json"),
                      "services": report.get("final_services", report.get("services"))}, indent=2))
