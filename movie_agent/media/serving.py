"""HTTP byte serving for registered media artifacts, including one RFC 7233 range."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from movie_agent.domain import Artifact
from movie_agent.media.preview import media_metadata
from movie_agent.media.storage import LocalBinaryArtifactStore


def _range(value: str | None, size: int) -> tuple[int, int, bool]:
    if not value:
        return 0, size - 1, False
    if not value.startswith("bytes=") or "," in value:
        raise ValueError("only one byte range is supported")
    spec = value[6:]
    if "-" not in spec:
        raise ValueError("invalid byte range")
    start_text, end_text = spec.split("-", 1)
    if not start_text:
        length = int(end_text)
        if length <= 0:
            raise ValueError("invalid suffix range")
        return max(0, size - length), size - 1, True
    start = int(start_text)
    end = int(end_text) if end_text else size - 1
    if start < 0 or start >= size or end < start:
        raise ValueError("unsatisfiable byte range")
    return start, min(end, size - 1), True


def media_response(
    request: Request,
    artifact: Artifact,
    store: LocalBinaryArtifactStore,
) -> StreamingResponse:
    metadata = media_metadata(artifact)
    if metadata is None or not store.exists(artifact.uri):
        raise HTTPException(404, "Artifact has no registered binary content")
    record = store.describe(artifact.uri)
    try:
        start, end, partial = _range(request.headers.get("range"), record.size)
    except (ValueError, TypeError):
        raise HTTPException(
            416,
            "Requested range is not satisfiable",
            headers={"Content-Range": f"bytes */{record.size}", "Accept-Ranges": "bytes"},
        )

    def body() -> Iterator[bytes]:
        remaining = end - start + 1
        with store.open(artifact.uri) as stream:
            stream.seek(start)
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    return
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    }
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{record.size}"
    return StreamingResponse(
        body(), status_code=206 if partial else 200,
        media_type=record.mime_type, headers=headers,
    )

