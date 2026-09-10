"""Binary artifact storage; domain and API callers use artifact:// URIs only."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from io import BufferedReader, BytesIO
from pathlib import Path
from threading import RLock
from typing import BinaryIO
from urllib.parse import urlparse


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}")
_SAFE_EXTENSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,15}")
_SAFE_MIME = re.compile(r"(?:(image|video|audio)/[A-Za-z0-9.+-]+|application/json|application/x-subrip|text/plain)")


def artifact_uri(artifact_id: str, version: int) -> str:
    if not _SAFE_ID.fullmatch(artifact_id) or version < 1:
        raise ValueError("invalid artifact identity")
    return f"artifact://{artifact_id}/v{version}"


def parse_artifact_uri(uri: str) -> tuple[str, int]:
    parsed = urlparse(uri)
    if parsed.scheme != "artifact" or not _SAFE_ID.fullmatch(parsed.netloc):
        raise ValueError("invalid artifact URI")
    match = re.fullmatch(r"/v([1-9][0-9]*)", parsed.path)
    if not match or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("invalid artifact URI")
    return parsed.netloc, int(match.group(1))


@dataclass(frozen=True)
class BinaryArtifactRecord:
    uri: str
    mime_type: str
    size: int
    extension: str
    path: Path


class BinaryArtifactStore(ABC):
    @abstractmethod
    def put(
        self,
        artifact_id: str,
        version: int,
        source: bytes | BinaryIO,
        *,
        mime_type: str,
        extension: str,
    ) -> BinaryArtifactRecord: ...

    @abstractmethod
    def open(self, uri: str) -> BinaryIO: ...

    @abstractmethod
    def exists(self, uri: str) -> bool: ...

    @abstractmethod
    def size(self, uri: str) -> int: ...

    @abstractmethod
    def delete_if_unreferenced(self, uri: str, referenced_uris: set[str]) -> bool: ...


class LocalBinaryArtifactStore(BinaryArtifactStore):
    """Local immutable media objects with an adapter-private path index."""

    def __init__(self, root: str | Path, *, max_size_bytes: int = 2 * 1024**3) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self.max_size_bytes = max_size_bytes
        self._lock = RLock()
        self._index: dict[str, dict[str, object]] = self._load()

    def _load(self) -> dict[str, dict[str, object]]:
        if not self.index_path.exists():
            return {}
        payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("binary artifact index is invalid")
        return payload

    def _persist(self) -> None:
        temporary = self.index_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._index, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.index_path)

    @staticmethod
    def _validate(artifact_id: str, version: int, mime_type: str, extension: str) -> str:
        if not _SAFE_ID.fullmatch(artifact_id):
            raise ValueError("artifact_id must be a safe opaque path component")
        if version < 1:
            raise ValueError("artifact version must be positive")
        extension = extension.lstrip(".").lower()
        if not _SAFE_EXTENSION.fullmatch(extension):
            raise ValueError("invalid binary artifact extension")
        if not _SAFE_MIME.fullmatch(mime_type):
            raise ValueError("unsupported media MIME type")
        return extension

    def put(
        self,
        artifact_id: str,
        version: int,
        source: bytes | BinaryIO,
        *,
        mime_type: str,
        extension: str,
    ) -> BinaryArtifactRecord:
        extension = self._validate(artifact_id, version, mime_type, extension)
        uri = artifact_uri(artifact_id, version)
        with self._lock:
            if uri in self._index:
                raise FileExistsError(f"binary artifact already exists: {uri}")
            artifact_dir = (self.root / artifact_id).resolve()
            if self.root not in artifact_dir.parents:
                raise ValueError("binary artifact path escapes its root")
            artifact_dir.mkdir(parents=True, exist_ok=True)
            target = artifact_dir / f"v{version}.{extension}"
            temporary = target.with_suffix(target.suffix + ".tmp")
            total = 0
            stream = BytesIO(source) if isinstance(source, bytes) else source
            with temporary.open("wb") as output:
                while chunk := stream.read(1024 * 1024):
                    total += len(chunk)
                    if total > self.max_size_bytes:
                        output.close()
                        temporary.unlink(missing_ok=True)
                        raise ValueError("binary artifact exceeds configured size limit")
                    output.write(chunk)
            if target.exists():
                temporary.unlink(missing_ok=True)
                raise FileExistsError(target)
            temporary.replace(target)
            relative = target.relative_to(self.root).as_posix()
            self._index[uri] = {
                "relative_path": relative,
                "mime_type": mime_type,
                "size": total,
                "extension": extension,
            }
            self._persist()
            return BinaryArtifactRecord(uri, mime_type, total, extension, target)

    def describe(self, uri: str) -> BinaryArtifactRecord:
        parse_artifact_uri(uri)
        try:
            item = self._index[uri]
        except KeyError as error:
            raise KeyError(uri) from error
        path = (self.root / str(item["relative_path"])).resolve()
        if self.root not in path.parents or not path.is_file():
            raise KeyError(uri)
        return BinaryArtifactRecord(uri, str(item["mime_type"]), int(item["size"]),
                                    str(item["extension"]), path)

    def open(self, uri: str) -> BufferedReader:
        return self.describe(uri).path.open("rb")

    def exists(self, uri: str) -> bool:
        try:
            self.describe(uri)
            return True
        except (KeyError, ValueError):
            return False

    def size(self, uri: str) -> int:
        return self.describe(uri).size

    def delete_if_unreferenced(self, uri: str, referenced_uris: set[str]) -> bool:
        if uri in referenced_uris:
            return False
        with self._lock:
            record = self.describe(uri)
            record.path.unlink(missing_ok=True)
            del self._index[uri]
            self._persist()
            return True
