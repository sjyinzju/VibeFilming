"""Versioned artifact storage independent of business-layer filesystem paths."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from threading import RLock

from movie_agent.domain import (
    Artifact,
    ArtifactType,
    JSONValue,
    Provenance,
    new_id,
)


class ArtifactStore(ABC):
    """Interface for immutable artifact versions and selection state."""

    @abstractmethod
    def register(self, artifact: Artifact) -> Artifact: ...

    @abstractmethod
    def get(self, artifact_id: str, version: int | None = None) -> Artifact | None: ...

    @abstractmethod
    def list_versions(self, artifact_id: str) -> list[Artifact]: ...

    @abstractmethod
    def select(self, artifact_id: str, version: int) -> Artifact: ...

    @abstractmethod
    def create_structured(self, artifact_id: str, content: JSONValue, *, artifact_type=ArtifactType.TEXT,
                          source_job_id=None, parent_artifact_ids=None, metadata=None, provenance=None) -> Artifact: ...

    @abstractmethod
    def read_structured(self, artifact: Artifact) -> JSONValue: ...


class LocalArtifactStore(ArtifactStore):
    """Local manifest and immutable files suitable for tests and development."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.data_dir = self.root / "data"
        self.manifest_path = self.root / "manifest.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._artifacts: list[Artifact] = self._load()

    def _load(self) -> list[Artifact]:
        if not self.manifest_path.exists():
            return []
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        return [Artifact.model_validate(item) for item in data]

    def _persist(self) -> None:
        temporary = self.manifest_path.with_suffix(".tmp")
        payload = [artifact.model_dump(mode="json") for artifact in self._artifacts]
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.manifest_path)

    def register(self, artifact: Artifact) -> Artifact:
        with self._lock:
            versions = self.list_versions(artifact.artifact_id)
            expected = versions[-1].version + 1 if versions else 1
            if artifact.version != expected:
                raise ValueError(f"artifact version must be {expected}")
            if any(item.uri == artifact.uri for item in versions):
                raise ValueError("new artifact versions must not overwrite a prior URI")
            self._artifacts.append(artifact)
            self._persist()
            return artifact

    def create_placeholder(
        self,
        artifact_type: ArtifactType,
        content: JSONValue,
        *,
        artifact_id: str | None = None,
        source_job_id: str | None = None,
        parent_artifact_ids: list[str] | None = None,
        metadata: dict[str, JSONValue] | None = None,
        provenance: Provenance | None = None,
        extension: str = "json",
    ) -> Artifact:
        """Materialize a small placeholder and register a never-overwritten version."""

        stable_id = artifact_id or new_id("artifact")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}", stable_id):
            raise ValueError("artifact_id must be a safe opaque path component")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", extension.lstrip(".")):
            raise ValueError("artifact extension must not contain path separators")
        with self._lock:
            version = len(self.list_versions(stable_id)) + 1
            artifact_dir = self.data_dir / stable_id
            artifact_dir.mkdir(parents=True, exist_ok=True)
            file_path = artifact_dir / f"v{version}.{extension.lstrip('.')}"
            if file_path.exists():
                raise FileExistsError(file_path)
            if isinstance(content, str):
                file_path.write_text(content, encoding="utf-8")
            else:
                file_path.write_text(
                    json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            artifact = Artifact(
                artifact_id=stable_id,
                artifact_type=artifact_type,
                uri=file_path.as_uri(),
                version=version,
                source_job_id=source_job_id,
                parent_artifact_ids=parent_artifact_ids or [],
                metadata=metadata or {},
                provenance=provenance or Provenance(),
            )
            return self.register(artifact)

    def get(self, artifact_id: str, version: int | None = None) -> Artifact | None:
        versions = self.list_versions(artifact_id)
        if not versions:
            return None
        if version is None:
            selected = next((artifact for artifact in versions if artifact.selected), None)
            return selected or versions[-1]
        return next((artifact for artifact in versions if artifact.version == version), None)

    def create_structured(self, artifact_id: str, content: JSONValue, *, artifact_type=ArtifactType.TEXT,
                          source_job_id=None, parent_artifact_ids=None, metadata=None, provenance=None) -> Artifact:
        """Commit structured production evidence using the existing manifest and URI scheme."""
        from movie_agent.media.storage import artifact_uri
        with self._lock:
            versions = self.list_versions(artifact_id)
            version = versions[-1].version + 1 if versions else 1
            uri = artifact_uri(artifact_id, version)
            folder = self.data_dir / artifact_id
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"v{version}.json"
            # An unregistered crash orphan is safe to replace; registered versions
            # always get a different name and are never overwritten.
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(target)
            return self.register(Artifact(artifact_id=artifact_id, artifact_type=artifact_type, uri=uri,
                version=version, source_job_id=source_job_id, parent_artifact_ids=parent_artifact_ids or [],
                metadata={"mime_type": "application/json", **(metadata or {})}, provenance=provenance or Provenance()))

    def read_structured(self, artifact: Artifact) -> JSONValue:
        from movie_agent.media.storage import parse_artifact_uri
        identity, version = parse_artifact_uri(artifact.uri)
        if identity != artifact.artifact_id or version != artifact.version:
            raise ValueError("structured evidence URI mismatch")
        return json.loads((self.data_dir / identity / f"v{version}.json").read_text(encoding="utf-8"))

    def list_versions(self, artifact_id: str) -> list[Artifact]:
        return sorted(
            (artifact for artifact in self._artifacts if artifact.artifact_id == artifact_id),
            key=lambda artifact: artifact.version,
        )

    def list_all(self) -> list[Artifact]:
        """Return a detached copy of all registered immutable versions."""

        return [artifact.model_copy(deep=True) for artifact in self._artifacts]

    def select(self, artifact_id: str, version: int) -> Artifact:
        with self._lock:
            target: Artifact | None = None
            updated: list[Artifact] = []
            for artifact in self._artifacts:
                if artifact.artifact_id == artifact_id:
                    selected = artifact.version == version
                    artifact = artifact.model_copy(update={"selected": selected})
                    if selected:
                        target = artifact
                updated.append(artifact)
            if target is None:
                raise KeyError(f"unknown artifact version {artifact_id}@{version}")
            self._artifacts = updated
            self._persist()
            return target
