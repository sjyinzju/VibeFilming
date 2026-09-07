"""Reusable artifact-to-multipart transport for future remote media providers."""

from __future__ import annotations

from dataclasses import dataclass
import re

import httpx

from movie_agent.artifacts import ArtifactStore
from movie_agent.media.contracts import ImageGenerationRequest, MediaReference, VideoGenerationRequest
from movie_agent.media.storage import BinaryArtifactStore


@dataclass(frozen=True)
class ResolvedMediaReference:
    reference: MediaReference
    filename: str
    mime_type: str
    content: bytes


class MediaReferenceBinaryResolver:
    """Resolve opaque artifact identities at the provider boundary, never in prompts."""

    def __init__(self, artifacts: ArtifactStore, binaries: BinaryArtifactStore) -> None:
        self.artifacts = artifacts
        self.binaries = binaries

    def resolve(self, reference: MediaReference) -> ResolvedMediaReference:
        artifact = self.artifacts.get(reference.artifact_id, reference.version)
        if artifact is None:
            raise KeyError(reference.artifact_id)
        mime_type = str(artifact.metadata.get("mime_type", ""))
        if not mime_type.startswith("image/"):
            raise ValueError("media reference artifact is not an image")
        extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime_type)
        if extension is None:
            raise ValueError("media reference MIME type is not transportable")
        filename = reference.original_filename or f"{reference.artifact_id}.{extension}"
        filename = filename.replace("\\", "_").replace("/", "_")
        filename = re.sub(r'[\x00-\x1f\x7f"]', "_", filename)[:255]
        with self.binaries.open(artifact.uri) as stream:
            content = stream.read()
        return ResolvedMediaReference(reference.model_copy(update={"version": artifact.version}), filename, mime_type, content)


class MultipartMediaEncoder:
    """Create one stable multipart contract shared by image and video adapters."""

    def __init__(self, resolver: MediaReferenceBinaryResolver) -> None:
        self.resolver = resolver

    @staticmethod
    def _part(field: str, resolved: ResolvedMediaReference, role: str):
        reference = resolved.reference
        headers = {
            "X-Artifact-Id": reference.artifact_id,
            "X-Artifact-Version": str(reference.version or "selected"),
            "X-Reference-Type": reference.reference_type.value,
            "X-Reference-Role": role,
        }
        return (field, (resolved.filename, resolved.content, resolved.mime_type, headers))

    def image(self, request: ImageGenerationRequest):
        files = [self._part("references", self.resolver.resolve(item), "reference")
                 for item in request.references]
        if request.source_image and all(
            item.artifact_id != request.source_image.artifact_id for item in request.references
        ):
            files.append(self._part("source_image", self.resolver.resolve(request.source_image),
                                    "source_image"))
        return {"request": request.model_dump_json()}, files

    def video(self, request: VideoGenerationRequest):
        files = []
        special: set[tuple[str, int | None, object]] = set()
        for field, reference in (
            ("first_frame", request.first_frame),
            ("last_frame", request.last_frame),
            ("previous_shot", request.previous_shot),
        ):
            if reference:
                files.append(self._part(field, self.resolver.resolve(reference), field))
                special.add((reference.artifact_id, reference.version, reference.reference_type))
        files.extend(
            self._part("references", self.resolver.resolve(item), "reference")
            for item in request.references
            if (item.artifact_id, item.version, item.reference_type) not in special
        )
        return {"request": request.model_dump_json()}, files


class ArtifactMultipartTransport:
    """HTTP transport helper; real providers supply their own authenticated client."""

    def __init__(self, client: httpx.AsyncClient, encoder: MultipartMediaEncoder) -> None:
        self.client = client
        self.encoder = encoder

    async def post_image(self, url: str, request: ImageGenerationRequest) -> httpx.Response:
        data, files = self.encoder.image(request)
        response = await self.client.post(url, data=data, files=files)
        response.raise_for_status()
        return response

    async def post_video(self, url: str, request: VideoGenerationRequest) -> httpx.Response:
        data, files = self.encoder.video(request)
        response = await self.client.post(url, data=data, files=files)
        response.raise_for_status()
        return response
