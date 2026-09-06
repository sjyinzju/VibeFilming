"""Full Fake media pipeline exercises every P3 runtime layer."""

from __future__ import annotations

import asyncio
from pathlib import Path

from movie_agent.domain import ArtifactType, EventType
from movie_agent.services import MockMovieProduction


FIXTURE = Path(__file__).parent / "fixtures" / "sample_brief.json"


def test_full_fake_media_pipeline_uses_binary_jobs_qc_repair_and_post(tmp_path: Path) -> None:
    production = MockMovieProduction(tmp_path / "p3")
    result = asyncio.run(production.run(production.load_brief(FIXTURE)))

    media = [item for item in result.artifacts if item.artifact_type in {
        ArtifactType.FRAME, ArtifactType.IMAGE, ArtifactType.VIDEO,
        ArtifactType.AUDIO, ArtifactType.FINAL_FILM,
    }]
    assert result.completed and media
    assert all(item.uri.startswith("artifact://") for item in media)
    assert all(production.binary_store.exists(item.uri) for item in media)
    assert production.binary_store.open("artifact://frame_shot_001_first/v1").read(8) == b"\x89PNG\r\n\x1a\n"
    assert production.binary_store.open("artifact://video_shot_001/v1").read(8)[4:] == b"ftyp"
    assert production.binary_store.open("artifact://audio_music/v1").read(4) == b"RIFF"
    assert production.binary_store.open("artifact://final_film/v1").read(8)[4:] == b"ftyp"
    assert len(production.artifact_store.list_versions("video_shot_002")) == 2
    assert production.artifact_store.get("video_shot_002").selected
    assert result.media_inspections and result.media_repair_plans and result.timeline
    assert result.timeline.video_tracks and len(result.timeline.audio_tracks) == 6
    event_types = {item.event_type for item in result.event_stream}
    assert {EventType.MEDIA_JOB_CREATED, EventType.MEDIA_JOB_STARTED,
            EventType.MEDIA_JOB_COMPLETED, EventType.MEDIA_EVALUATION_STARTED,
            EventType.MEDIA_EVALUATION_COMPLETED, EventType.MEDIA_REPAIR_STARTED,
            EventType.MEDIA_REPAIR_COMPLETED}.issubset(event_types)
    assert all(job.progress in {0.0, 1.0} for job in result.jobs)
    assert all(not job.progress_is_determinate for job in result.jobs if job.task != "structured_text")
    final = production.artifact_store.get("final_film")
    assert final.provenance.provider_id == "mock-post"
    assert final.provenance.evaluation_ids

