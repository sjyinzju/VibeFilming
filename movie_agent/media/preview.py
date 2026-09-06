"""Preview/thumbnail resolution without leaking local filesystem paths."""

from __future__ import annotations

from pydantic import Field

from movie_agent.domain import Artifact, ArtifactType, ContractModel
from movie_agent.media.contracts import MediaArtifactMetadata


class MediaPreview(ContractModel):
    artifact_id: str
    version: int
    content_url: str
    preview_url: str
    thumbnail_url: str | None = None
    playable: bool = False
    mime_type: str
    waveform: list[float] = Field(default_factory=list)


def media_metadata(artifact: Artifact) -> MediaArtifactMetadata | None:
    payload = artifact.metadata.get("media")
    if not isinstance(payload, dict):
        return None
    return MediaArtifactMetadata.model_validate(payload)


class PreviewService:
    def describe(self, project_id: str, artifact: Artifact) -> MediaPreview | None:
        metadata = media_metadata(artifact)
        if metadata is None:
            return None
        query = f"?project_id={project_id}&version={artifact.version}"
        base = f"/artifacts/{artifact.artifact_id}"
        thumbnail = None
        if metadata.thumbnail_artifact_id:
            thumbnail = f"/artifacts/{metadata.thumbnail_artifact_id}/content?project_id={project_id}"
        elif artifact.artifact_type in {ArtifactType.IMAGE, ArtifactType.FRAME}:
            thumbnail = f"{base}/thumbnail{query}"
        return MediaPreview(
            artifact_id=artifact.artifact_id,
            version=artifact.version,
            content_url=f"{base}/content{query}",
            preview_url=f"{base}/preview{query}",
            thumbnail_url=thumbnail,
            playable=artifact.artifact_type in {ArtifactType.IMAGE, ArtifactType.FRAME,
                                                  ArtifactType.VIDEO, ArtifactType.AUDIO,
                                                  ArtifactType.FINAL_FILM},
            mime_type=metadata.encoding.mime_type,
            waveform=metadata.waveform,
        )

