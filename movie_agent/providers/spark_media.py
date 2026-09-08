"""Narrow content-addressed data plane. No caller-supplied remote commands."""

import asyncio
from dataclasses import dataclass
from hashlib import sha256
import io
import json
import re
import shlex

from PIL import Image
from movie_agent.domain import ProviderErrorType
from movie_agent.providers.base import ProviderFailure

MIME_EXTENSIONS = {"video/mp4": "mp4", "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


@dataclass(frozen=True)
class StagedMedia:
    sha256: str
    size_bytes: int
    mime_type: str
    url: str
    deduplicated: bool


# Constant, audited protocol; argv contains only validated root/hash/extension/limits.
# Lock + atomic replace protect concurrent writers. TTL never removes recent/active
# files; a full cache rejects admission instead of deleting another request's input.
_REMOTE_STAGE = r'''
import sys, os, re, json, hashlib, time, fcntl
from pathlib import Path
root, digest, ext, length, ttl, capacity = sys.argv[1:]
length, ttl, capacity = int(length), int(ttl), int(capacity)
root = Path(root)
assert root.is_absolute() and re.fullmatch(r"[a-f0-9]{64}", digest) and ext in {"mp4","png","jpg","webp"}
root.mkdir(parents=True, exist_ok=True)
assert not root.is_symlink()
with (root / ".p4b-staging.lock").open("a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    files = [p for p in root.iterdir() if re.fullmatch(r"[a-f0-9]{64}\.(mp4|png|jpg|webp)", p.name) and p.is_file() and not p.is_symlink()]
    now = time.time()
    for p in files:
        if now - p.stat().st_mtime > ttl:
            p.unlink()
    files = [p for p in files if p.exists()]
    target = root / (digest + "." + ext)
    assert not target.is_symlink()
    data = sys.stdin.buffer.read(length + 1)
    assert len(data) == length and hashlib.sha256(data).hexdigest() == digest
    dedup = target.exists() and target.stat().st_size == length and hashlib.sha256(target.read_bytes()).hexdigest() == digest
    if not dedup:
        assert sum(p.stat().st_size for p in files) + length <= capacity, "staging_capacity_exhausted"
        tmp = root / (".upload-" + digest)
        assert not tmp.is_symlink()
        with tmp.open("wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, target)
    os.utime(target, None)
    print(json.dumps({"sha256": digest, "size_bytes": length, "deduplicated": dedup}))
'''


class SparkMediaStager:
    def __init__(self, settings, *, remote_root="/home/Developer/runtime/vlm-media",
                 max_size_bytes=256 * 1024**2, ttl_seconds=86400,
                 capacity_bytes=8 * 1024**3, timeout=120, runner=None):
        if not re.fullmatch(r"/home/Developer/runtime/[A-Za-z0-9_-]+", remote_root):
            raise ValueError("media root must be a single directory under the Spark runtime root")
        if max_size_bytes < 1 or capacity_bytes < max_size_bytes or ttl_seconds < 3600:
            raise ValueError("invalid staging capacity/retention")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", settings.ssh_host):
            raise ValueError("invalid Spark host")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", settings.ssh_user):
            raise ValueError("invalid Spark user")
        self.settings, self.remote_root = settings, remote_root
        self.max_size_bytes, self.ttl_seconds = max_size_bytes, ttl_seconds
        self.capacity_bytes, self.timeout = capacity_bytes, timeout
        self.runner = runner or self._transfer

    async def _transfer(self, content, digest, extension):
        args = ["ssh", "-p", str(self.settings.ssh_port), "-i", self.settings.ssh_key_path,
                "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=10",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
                f"{self.settings.ssh_user}@{self.settings.ssh_host}"]
        command = shlex.join(["python3", "-c", _REMOTE_STAGE, self.remote_root, digest, extension,
                              str(len(content)), str(self.ttl_seconds), str(self.capacity_bytes)])
        process = await asyncio.create_subprocess_exec(*args, command, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, _ = await asyncio.wait_for(process.communicate(content), self.timeout)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise ProviderFailure("media staging unavailable or capacity exhausted", ProviderErrorType.UNAVAILABLE)
        return json.loads(out)

    async def stage(self, content: bytes, mime_type: str) -> StagedMedia:
        if mime_type not in MIME_EXTENSIONS or not 0 < len(content) <= self.max_size_bytes:
            raise ProviderFailure("invalid staging MIME or size", ProviderErrorType.INVALID_REQUEST)
        if mime_type == "video/mp4":
            if len(content) < 12 or content[4:8] != b"ftyp":
                raise ProviderFailure("invalid MP4 signature", ProviderErrorType.MEDIA_CORRUPT)
        else:
            try:
                with Image.open(io.BytesIO(content)) as img:
                    if Image.MIME.get(img.format) != mime_type:
                        raise ValueError("MIME mismatch")
                    img.verify()
            except Exception as error:
                raise ProviderFailure("invalid image bytes", ProviderErrorType.MEDIA_CORRUPT) from error
        digest, extension = sha256(content).hexdigest(), MIME_EXTENSIONS[mime_type]
        try:
            result = await self.runner(content, digest, extension)
            if result["sha256"] != digest or result["size_bytes"] != len(content):
                raise ValueError("staging checksum mismatch")
        except ProviderFailure:
            raise
        except (TimeoutError, OSError, ValueError, KeyError) as error:
            raise ProviderFailure("media staging failed before VLM submission", ProviderErrorType.UNAVAILABLE) from error
        return StagedMedia(digest, len(content), mime_type, f"file:///media/{digest}.{extension}",
                           result["deduplicated"])
