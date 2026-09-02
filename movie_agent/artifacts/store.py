"""Versioned artifact storage independent of business-layer filesystem paths."""

from __future__ import annotations

import json
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

