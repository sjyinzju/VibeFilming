"""Explicit opt-in real H3 provider acceptance using existing FLUX frame Artifacts."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from movie_agent.artifacts import LocalArtifactStore
from movie_agent.comfyui import ComfyUIClient, ComfyUIWorkflowRegistry
from movie_agent.domain import ArtifactType, CheckpointSnapshot, GenerationJob, GenerationStrategyType, JobStatus, PromptPackage, Provenance, ResourceClass
from movie_agent.execution import JobManager
from movie_agent.execution.checkpoints import LocalCheckpointStore
from movie_agent.execution.durable_events import DurableLocalEventBus
from movie_agent.media import (
    AudioCue,
    AudioPurpose,
    AudioTrack,
    CameraMotionSpec,
    MediaReference,
    ReferenceType,
    Timeline,
    TimelineClip,
    VideoCapabilities,
    VideoGenerationMode,
    VideoGenerationRequest,
    VideoTrack,
)
from movie_agent.media.runtime import MediaRuntime
from movie_agent.media.storage import LocalBinaryArtifactStore
from movie_agent.media.transport import MediaReferenceBinaryResolver
from movie_agent.providers.comfyui_video import ComfyUIVideoProvider
from movie_agent.providers.registry import ProviderRegistry


DEFAULT_PROJECT = Path(
    "workspace/flux-agent-acceptance-20260907/"
    "project_ba41e17ddc724deab7802abf545719fa"
)


async def accept(project_root: Path, endpoint: str) -> dict[str, object]:
    artifacts = LocalArtifactStore(project_root / "artifacts")
    binaries = LocalBinaryArtifactStore(project_root / "artifacts" / "media")
    first_id = "frame_scene_01-SHOT-01_first"
    last_id = "frame_scene_01-SHOT-01_last"
    first_artifact = artifacts.get(first_id)
    last_artifact = artifacts.get(last_id)
    if first_artifact is None or last_artifact is None:
        raise RuntimeError("existing real FLUX first/last frame Artifacts are unavailable")
    first = MediaReference(
        reference_type=ReferenceType.FIRST_FRAME,
        artifact_id=first_id,
        version=first_artifact.version,
        scene_id="scene_01",
        shot_id="scene_01-SHOT-01",
    )
    last = MediaReference(
        reference_type=ReferenceType.LAST_FRAME,
        artifact_id=last_id,
        version=last_artifact.version,
        scene_id="scene_01",
        shot_id="scene_01-SHOT-01",
    )
    client = ComfyUIClient(endpoint=endpoint, timeout=1800, websocket_timeout=1800)
    provider = ComfyUIVideoProvider(
        client=client,
        workflows=ComfyUIWorkflowRegistry(),
        workflow_profile="minimax_h3_fl2va",
        resolver=MediaReferenceBinaryResolver(artifacts, binaries),
        runtime_capabilities=VideoCapabilities(
            first_frame=True,
            last_frame=True,
            first_last_frame=True,
            audio_generation=True,
            max_duration_seconds=15,
            max_width=1344,
            max_height=1344,
            supported_fps=[24],
        ),
    )
    providers = ProviderRegistry()
    providers.register(provider)
    events = DurableLocalEventBus(project_root / "events")
    runtime = MediaRuntime(artifacts, binaries, providers, events, "trace_h3_real_acceptance")
    jobs = JobManager(events, "trace_h3_real_acceptance")
    runtime.bind_jobs(jobs)
    output_id = "video_h3_scene_01-SHOT-01"
    request = VideoGenerationRequest(
        job_id="video:h3-real-acceptance:v1",
        project_id="project_ba41e17ddc724deab7802abf545719fa",
        scene_id="scene_01",
        shot_id="scene_01-SHOT-01",
        output_artifact_id=output_id,
        prompt_package=PromptPackage(
            compiler_id="generic-video",
            compiler_version="1.0.0",
            positive_prompt=(
                "Warm cinematic kitchen scene. The woman makes a small natural movement and "
                "looks toward the teal kettle while sunlight shifts gently. Preserve identity, "
                "composition, and photorealism between the supplied first and last frames. "
                "Audio: a soft ceramic kettle lid click, quiet room tone, and a brief gentle chime."
            ),
            negative_prompt="identity drift, temporal flicker, added people, text overlays",
            source_shot_id="scene_01-SHOT-01",
        ),
        references=[first, last],
        first_frame=first,
        last_frame=last,
        mode=VideoGenerationMode.FIRST_LAST_FRAME_TO_VIDEO,
        duration_seconds=22 / 24,
        fps=24,
        width=608,
        height=352,
        aspect_ratio="16:9",
        seed=20260907,
        camera_motion=CameraMotionSpec(motion_type="static"),
        required_capabilities=[],
        resource_class=ResourceClass.EXCLUSIVE,
    )
    job = GenerationJob(
        job_id=request.job_id,
        project_id=request.project_id,
        scene_id=request.scene_id,
        shot_id=request.shot_id,
        node_id="shot_production",
        task="video",
        strategy_type=GenerationStrategyType.FIRST_LAST_FRAME_TO_VIDEO.value,
        resource_class=ResourceClass.EXCLUSIVE,
        idempotency_key=request.job_id,
        input_artifact_ids=[first_id, last_id],
        provenance=Provenance(
            role="Showrunner",
            tool="media_runtime",
            project_id=request.project_id,
            scene_id=request.scene_id,
            shot_id=request.shot_id,
            input_artifact_ids=[first_id, last_id],
        ),
    )
    video = await runtime.generate_video(job, request)
    completed = jobs.get(job.job_id)
    audio = next(
        item for item in artifacts.list_all()
        if item.source_job_id == job.job_id
        and item.artifact_type == ArtifactType.AUDIO
        and item.metadata.get("media", {}).get("purpose")
        == AudioPurpose.GENERATED_NATIVE_AUDIO.value
    )
    duration = float(video.metadata.get("duration_seconds") or request.duration_seconds)
    timeline = Timeline(
        timeline_id="timeline_h3_real_acceptance",
        project_id=request.project_id,
        duration_seconds=duration,
        video_tracks=[VideoTrack(clips=[TimelineClip(
            artifact_id=video.artifact_id,
            start_time_seconds=0,
            duration_seconds=duration,
            shot_id=request.shot_id,
        )])],
        audio_tracks=[AudioTrack(cues=[AudioCue(
            artifact_id=audio.artifact_id,
            start_time_seconds=0,
            duration_seconds=float(audio.metadata.get("duration_seconds") or duration),
            cue_type=AudioPurpose.GENERATED_NATIVE_AUDIO,
            scene_id=request.scene_id,
            shot_id=request.shot_id,
        )])],
    )
    timeline_artifact = artifacts.create_placeholder(
        ArtifactType.TIMELINE,
        timeline.model_dump(mode="json"),
        artifact_id="timeline_h3_real_acceptance",
        source_job_id=job.job_id,
        parent_artifact_ids=[video.artifact_id, audio.artifact_id],
        provenance=Provenance(
            role="Sound/Post Director",
            tool="native_audio_timeline_projection",
            provider_id=provider.provider_id,
            project_id=request.project_id,
            scene_id=request.scene_id,
            shot_id=request.shot_id,
            input_artifact_ids=[video.artifact_id, audio.artifact_id],
        ),
    )
    metadata = video.provenance.parameters
    report = {
        "job_id": job.job_id,
        "job_status": completed.status.value,
        "remote_status": completed.remote_status.value if completed.remote_status else None,
        "comfyui_prompt_id": metadata.get("comfyui_prompt_id"),
        "workflow_template_id": metadata.get("workflow_template_id"),
        "workflow_template_version": metadata.get("workflow_template_version"),
        "workflow_hash": metadata.get("workflow_hash"),
        "binding_manifest_id": metadata.get("binding_manifest_id"),
        "binding_manifest_version": metadata.get("binding_manifest_version"),
        "model_profile": metadata.get("model_profile"),
        "input_artifacts": metadata.get("input_artifacts"),
        "video_artifact": video.model_dump(mode="json"),
        "native_audio_artifact": audio.model_dump(mode="json"),
        "timeline": timeline.model_dump(mode="json"),
        "timeline_artifact": timeline_artifact.model_dump(mode="json"),
        "provider_execution_graph": metadata.get("provider_execution_graph"),
        "timings": metadata.get("timings"),
    }
    report_path = project_root / "h3_real_acceptance.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**report, "report_path": str(report_path)}


def verify_checkpoint_resume(project_root: Path) -> dict[str, object]:
    report_path = project_root / "h3_real_acceptance.json"
    report = json.loads(report_path.read_text("utf-8"))
    project_id = "project_ba41e17ddc724deab7802abf545719fa"
    checkpoints = LocalCheckpointStore(project_root / "checkpoints")
    previous = checkpoints.latest(project_id)
    if previous is None:
        raise RuntimeError("existing canonical project checkpoint is unavailable")
    video_id = report["video_artifact"]["artifact_id"]
    audio_id = report["native_audio_artifact"]["artifact_id"]
    completed = GenerationJob(
        job_id=report["job_id"],
        project_id=project_id,
        node_id="shot_production",
        scene_id="scene_01",
        shot_id="scene_01-SHOT-01",
        task="video",
        provider_id="comfyui-video",
        strategy_type=GenerationStrategyType.FIRST_LAST_FRAME_TO_VIDEO.value,
        status=JobStatus.SUCCEEDED,
        remote_status=JobStatus.SUCCEEDED,
        progress=1,
        idempotency_key=report["job_id"],
        input_artifact_ids=[
            "frame_scene_01-SHOT-01_first", "frame_scene_01-SHOT-01_last"
        ],
        output_artifact_ids=[video_id, audio_id],
        related_artifact_ids=[video_id, audio_id],
    )
    artifacts = LocalArtifactStore(project_root / "artifacts")
    state = dict(previous.project_state)
    state["timeline"] = report["timeline"]
    saved = checkpoints.save(CheckpointSnapshot(
        project_id=project_id,
        workflow_graph=previous.workflow_graph,
        project_state=state,
        completed_node_ids=previous.completed_node_ids,
        active_jobs=[
            *[job for job in previous.active_jobs if job.job_id != completed.job_id],
            completed,
        ],
        artifacts=artifacts.list_all(),
        evaluations=previous.evaluations,
        repair_plans=previous.repair_plans,
        retry_state={**previous.retry_state, completed.job_id: 0},
    ))
    resumed = checkpoints.prepare_resume(saved)
    restored = next(job for job in resumed.active_jobs if job.job_id == completed.job_id)
    if restored.status != JobStatus.SUCCEEDED or restored.output_artifact_ids != [video_id, audio_id]:
        raise RuntimeError("completed H3 outputs were not preserved across checkpoint resume")
    result = {
        "checkpoint_id": saved.checkpoint_id,
        "restored_job_status": restored.status.value,
        "restored_output_artifact_ids": restored.output_artifact_ids,
        "requeued": False,
        "provider_reinvoked": False,
    }
    report["checkpoint_resume"] = result
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8188")
    parser.add_argument("--checkpoint-only", action="store_true")
    args = parser.parse_args()
    if args.checkpoint_only:
        print(json.dumps(verify_checkpoint_resume(args.project_root.resolve()), indent=2))
        return
    if os.environ.get("MOVIE_AGENT_RUN_H3_INTEGRATION") != "1":
        raise SystemExit("Set MOVIE_AGENT_RUN_H3_INTEGRATION=1 to authorize real H3 inference")
    report = asyncio.run(accept(args.project_root.resolve(), args.endpoint))
    print(json.dumps({
        key: report[key]
        for key in (
            "job_id", "job_status", "remote_status", "comfyui_prompt_id",
            "workflow_hash", "timings", "report_path",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
