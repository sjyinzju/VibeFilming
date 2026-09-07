"""Immutable Artifact input bridge for ComfyUI uploads."""

from __future__ import annotations

import hashlib

from movie_agent.media.contracts import MediaReference, VideoGenerationRequest
from movie_agent.media.transport import MediaReferenceBinaryResolver

from .client import ComfyUIClient
from .contracts import ComfyUIInputAsset


class ComfyUIAssetBridge:
    def __init__(
        self,
        client: ComfyUIClient,
        resolver: MediaReferenceBinaryResolver,
        *,
        subfolder: str = "movie-agent",
    ) -> None:
        self.client = client
        self.resolver = resolver
        self.subfolder = subfolder.strip("/")

    async def upload_video_inputs(self, request: VideoGenerationRequest) -> list[ComfyUIInputAsset]:
        pending: list[tuple[str, MediaReference]] = []
        for slot, reference in (
            ("first_frame", request.first_frame),
            ("last_frame", request.last_frame),
            ("previous_shot", request.previous_shot),
        ):
            if reference is not None:
                pending.append((slot, reference))
        special_ids = {item.artifact_id for _, item in pending}
        pending.extend(
            ("reference_images", reference)
            for reference in request.references
            if reference.artifact_id not in special_ids
        )

        uploaded: list[ComfyUIInputAsset] = []
        seen: set[tuple[str, str, int]] = set()
        for slot, reference in pending:
            resolved = self.resolver.resolve(reference)
            version = resolved.reference.version
            if version is None:
                raise ValueError("resolved media reference has no immutable version")
            key = (slot, resolved.reference.artifact_id, version)
            if key in seen:
                continue
            seen.add(key)
            digest = hashlib.sha256(resolved.content).hexdigest()
            extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[resolved.mime_type]
            filename = (
                f"{resolved.reference.artifact_id}_v{version}_{digest[:12]}.{extension}"
            )
            remote = await self.client.upload_image(
                filename=filename,
                content=resolved.content,
                mime_type=resolved.mime_type,
                subfolder=self.subfolder,
            )
            uploaded.append(ComfyUIInputAsset(
                semantic_slot=slot,
                artifact_id=resolved.reference.artifact_id,
                artifact_version=version,
                sha256=digest,
                filename=remote.name,
                subfolder=remote.subfolder,
                type=remote.type,
                mime_type=resolved.mime_type,
            ))
        return uploaded

