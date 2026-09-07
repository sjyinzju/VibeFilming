"""Validated user image uploads backed by the existing artifact stores."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import Field, model_validator

from movie_agent.artifacts import ArtifactStore, LocalArtifactStore
from movie_agent.domain import Artifact, ArtifactType, ContractModel, Provenance, new_id
from movie_agent.media.contracts import (
    MediaArtifactMetadata,
    MediaDimensions,
    MediaEncoding,
    MediaModality,
    MediaReference,
    ReferenceBindingScope,
    ReferencePurpose,
    ReferenceType,
)
from movie_agent.media.references import ReferenceBank
from movie_agent.media.storage import BinaryArtifactStore, LocalBinaryArtifactStore


_DRAFT_ID = re.compile(r"draft_[A-Za-z0-9-]{8,80}")
_MIME_FORMAT = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
_FORMAT_EXTENSION = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}


class ImageReferenceBindingInput(ContractModel):
    reference_type: ReferenceType
    binding_scope: ReferenceBindingScope
    purpose: ReferencePurpose
    project_id: str | None = None
    entity_id: str | None = None
    scene_id: str | None = None
    shot_id: str | None = None
    binding_key: str | None = None

    @model_validator(mode="after")
    def validate_target(self) -> "ImageReferenceBindingInput":
        required = {
            ReferenceBindingScope.CREATIVE_INPUT: self.binding_key,
            ReferenceBindingScope.ENTITY: self.entity_id,
            ReferenceBindingScope.SCENE: self.scene_id,
            ReferenceBindingScope.SHOT: self.shot_id,
            ReferenceBindingScope.FRAME: self.shot_id,
        }
        if self.binding_scope in required and not required[self.binding_scope]:
            raise ValueError(f"{self.binding_scope.value} reference requires a binding target")
        return self


class ImageReferenceUploadResult(ContractModel):
    artifact_id: str
    artifact_uri: str
    version: int = Field(ge=1)
    mime_type: str
    size_bytes: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    created_at: datetime
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    preview_url: str
    thumbnail_url: str
    artifact: Artifact
    reference: MediaReference
    binding: ImageReferenceBindingInput


class ImageUploadValidationError(ValueError):
    pass


class ImageReferenceUploadService:
    """Own draft upload state and import it into a canonical project on submit."""

    def __init__(self, root: str | Path, *, max_size_bytes: int | None = None) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_size_bytes = max_size_bytes or int(
            os.environ.get("MOVIE_AGENT_IMAGE_UPLOAD_MAX_BYTES", str(20 * 1024**2))
        )

    def _draft_root(self, draft_id: str) -> Path:
        if not _DRAFT_ID.fullmatch(draft_id):
            raise KeyError(draft_id)
        target = (self.root / draft_id).resolve()
        if self.root not in target.parents:
            raise KeyError(draft_id)
        return target

    def _draft_stores(self, draft_id: str) -> tuple[LocalArtifactStore, LocalBinaryArtifactStore]:
        root = self._draft_root(draft_id)
        return LocalArtifactStore(root / "artifacts"), LocalBinaryArtifactStore(
            root / "artifacts" / "media", max_size_bytes=self.max_size_bytes
        )

    def _references_path(self, draft_id: str) -> Path:
        return self._draft_root(draft_id) / "references.json"

    def _draft_bank(self, draft_id: str) -> ReferenceBank:
        path = self._references_path(draft_id)
        if not path.exists():
            return ReferenceBank()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ReferenceBank([MediaReference.model_validate(item) for item in payload])

    def _save_draft_bank(self, draft_id: str, bank: ReferenceBank) -> None:
        path = self._references_path(draft_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps([item.model_dump(mode="json") for item in bank.all()], indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _filename(value: str | None) -> str:
        name = re.split(r"[/\\]", value or "upload")[-1].strip()
        name = re.sub(r'[\x00-\x1f\x7f"]', "_", name)
        return name[:255] or "upload"

    def _validate(self, content: bytes, declared_mime: str) -> tuple[str, int, int, Image.Image]:
        if not content:
            raise ImageUploadValidationError("Image file is empty")
        if len(content) > self.max_size_bytes:
            raise ImageUploadValidationError("Image exceeds the configured upload limit")
        expected = _MIME_FORMAT.get(declared_mime.lower())
        if expected is None:
            raise ImageUploadValidationError("Only PNG, JPEG, and WebP images are supported")
        try:
            with Image.open(io.BytesIO(content)) as probe:
                detected = probe.format
                probe.verify()
            image = Image.open(io.BytesIO(content))
            image.load()
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
                Image.DecompressionBombError) as error:
            raise ImageUploadValidationError("Image data is corrupt or cannot be decoded") from error
        if detected != expected:
            image.close()
            raise ImageUploadValidationError("Declared MIME type does not match the decoded image")
        width, height = image.size
        if width < 1 or height < 1:
            image.close()
            raise ImageUploadValidationError("Image dimensions are invalid")
        return detected, width, height, image

    def upload_draft(
        self,
        draft_id: str,
        content: bytes,
        *,
        filename: str | None,
        mime_type: str,
        binding: ImageReferenceBindingInput,
    ) -> ImageReferenceUploadResult:
        artifacts, binaries = self._draft_stores(draft_id)
        bank = self._draft_bank(draft_id)
        result = self._store(content, filename=filename, mime_type=mime_type, binding=binding,
                             artifacts=artifacts, binaries=binaries, owner_id=draft_id)
        result.reference = bank.add(result.reference)
        self._save_draft_bank(draft_id, bank)
        return result

    def upload_project(
        self,
        project_id: str,
        content: bytes,
        *,
        filename: str | None,
        mime_type: str,
        binding: ImageReferenceBindingInput,
        artifacts: ArtifactStore,
        binaries: BinaryArtifactStore,
        bank: ReferenceBank,
    ) -> ImageReferenceUploadResult:
        binding = binding.model_copy(update={"project_id": project_id})
        result = self._store(content, filename=filename, mime_type=mime_type, binding=binding,
                             artifacts=artifacts, binaries=binaries, owner_id=project_id)
        result.reference = bank.add(result.reference)
        return result

    def _store(
        self,
        content: bytes,
        *,
        filename: str | None,
        mime_type: str,
        binding: ImageReferenceBindingInput,
        artifacts: ArtifactStore,
        binaries: BinaryArtifactStore,
        owner_id: str,
    ) -> ImageReferenceUploadResult:
        detected, width, height, image = self._validate(content, mime_type)
        safe_filename = self._filename(filename)
        extension = _FORMAT_EXTENSION[detected]
        artifact_id = new_id("upload")
        version = 1
        thumbnail_id = f"thumbnail_{artifact_id}"
        original = binaries.put(artifact_id, version, content, mime_type=mime_type.lower(), extension=extension)
        media = MediaArtifactMetadata(
            modality=MediaModality.IMAGE,
            purpose=binding.purpose.value,
            dimensions=MediaDimensions(width=width, height=height, aspect_ratio=f"{width}:{height}"),
            encoding=MediaEncoding(mime_type=mime_type.lower(), format=extension),
            preview_artifact_id=artifact_id,
            thumbnail_artifact_id=thumbnail_id,
        )
        metadata = {
            "media": media.model_dump(mode="json"),
            "origin": "user_upload",
            "original_filename": safe_filename,
            "mime_type": mime_type.lower(),
            "size_bytes": original.size,
            "width": width,
            "height": height,
            "sha256": hashlib.sha256(content).hexdigest(),
            "binding": binding.model_dump(mode="json"),
        }
        artifact = Artifact(
            artifact_id=artifact_id,
            artifact_type=ArtifactType.IMAGE,
            uri=original.uri,
            version=version,
            metadata=metadata,
            provenance=Provenance(
                role="User",
                tool="user_upload",
                project_id=binding.project_id,
                parameters={"origin": "user_upload", "owner_id": owner_id,
                            "reference_purpose": binding.purpose.value},
            ),
        )
        artifacts.register(artifact)

        normalized = ImageOps.exif_transpose(image)
        normalized.thumbnail((480, 480))
        thumbnail_buffer = io.BytesIO()
        if "A" in normalized.getbands():
            normalized.save(thumbnail_buffer, format="PNG", optimize=True)
            thumbnail_mime, thumbnail_extension = "image/png", "png"
        else:
            normalized.convert("RGB").save(thumbnail_buffer, format="JPEG", quality=82, optimize=True)
            thumbnail_mime, thumbnail_extension = "image/jpeg", "jpg"
        image.close()
        thumbnail = binaries.put(thumbnail_id, 1, thumbnail_buffer.getvalue(),
                                 mime_type=thumbnail_mime, extension=thumbnail_extension)
        thumb_media = MediaArtifactMetadata(
            modality=MediaModality.IMAGE,
            purpose="thumbnail",
            dimensions=MediaDimensions(width=normalized.width, height=normalized.height,
                                       aspect_ratio=f"{normalized.width}:{normalized.height}"),
            encoding=MediaEncoding(mime_type=thumbnail_mime, format=thumbnail_extension),
            preview_artifact_id=thumbnail_id,
        )
        artifacts.register(Artifact(
            artifact_id=thumbnail_id,
            artifact_type=ArtifactType.IMAGE,
            uri=thumbnail.uri,
            version=1,
            parent_artifact_ids=[artifact_id],
            metadata={"media": thumb_media.model_dump(mode="json"), "origin": "derived_preview",
                      "mime_type": thumbnail_mime, "size_bytes": thumbnail.size},
            provenance=Provenance(role="Media Runtime", tool="image_thumbnail",
                                  project_id=binding.project_id, parent_artifact_id=artifact_id,
                                  input_artifact_ids=[artifact_id]),
        ))
        reference = MediaReference(
            reference_type=binding.reference_type,
            artifact_id=artifact_id,
            version=version,
            binding_scope=binding.binding_scope,
            purpose=binding.purpose,
            project_id=binding.project_id,
            entity_id=binding.entity_id,
            scene_id=binding.scene_id,
            shot_id=binding.shot_id,
            binding_key=binding.binding_key,
            original_filename=safe_filename,
            mime_type=mime_type.lower(),
            size_bytes=original.size,
            width=width,
            height=height,
        )
        return ImageReferenceUploadResult(
            artifact_id=artifact_id,
            artifact_uri=original.uri,
            version=version,
            mime_type=mime_type.lower(),
            size_bytes=original.size,
            width=width,
            height=height,
            created_at=artifact.created_at,
            sha256=metadata["sha256"],
            preview_url=f"/artifacts/{artifact_id}/preview",
            thumbnail_url=f"/artifacts/{artifact_id}/thumbnail",
            artifact=artifact,
            reference=reference,
            binding=binding,
        )

    def list_draft(self, draft_id: str) -> list[MediaReference]:
        return self._draft_bank(draft_id).all()

    def unbind_draft(self, draft_id: str, reference_id: str) -> MediaReference:
        bank = self._draft_bank(draft_id)
        reference = bank.unbind(reference_id)
        self._save_draft_bank(draft_id, bank)
        return reference

    def locate_draft_artifact(self, draft_id: str, artifact_id: str, version: int | None = None):
        artifacts, binaries = self._draft_stores(draft_id)
        artifact = artifacts.get(artifact_id, version)
        if artifact is None:
            raise KeyError(artifact_id)
        return artifact, binaries

    def adopt_draft(
        self,
        draft_id: str,
        project_id: str,
        *,
        artifacts: ArtifactStore,
        binaries: BinaryArtifactStore,
        bank: ReferenceBank,
    ) -> list[MediaReference]:
        source_artifacts, source_binaries = self._draft_stores(draft_id)
        adopted: list[MediaReference] = []
        for reference in self._draft_bank(draft_id).all():
            if not reference.selected:
                continue
            source = source_artifacts.get(reference.artifact_id, reference.version)
            if source is None:
                raise ImageUploadValidationError("Draft reference artifact is missing")
            related = [source]
            media = source.metadata.get("media", {})
            thumbnail_id = media.get("thumbnail_artifact_id") if isinstance(media, dict) else None
            if thumbnail_id:
                thumbnail = source_artifacts.get(str(thumbnail_id))
                if thumbnail:
                    related.append(thumbnail)
            for item in related:
                if artifacts.get(item.artifact_id, item.version):
                    continue
                with source_binaries.open(item.uri) as stream:
                    encoding = item.metadata.get("mime_type")
                    extension = source_binaries.describe(item.uri).extension
                    binaries.put(item.artifact_id, item.version, stream,
                                 mime_type=str(encoding), extension=extension)
                provenance = item.provenance.model_copy(update={"project_id": project_id})
                artifacts.register(item.model_copy(update={"provenance": provenance}, deep=True))
            bound = reference.model_copy(update={"project_id": project_id}, deep=True)
            adopted.append(bank.add(bound))
        return adopted
