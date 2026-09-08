"""Typed media provider interfaces and deterministic binary-producing mocks."""

from __future__ import annotations

import base64
import io
import math
import struct
import wave
import zlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

from movie_agent.domain import JobStatus, ProviderErrorType, ProviderKind, ProviderResult, ResourceClass, QualityProfile
from movie_agent.providers.base import ProviderFailure
from movie_agent.media.contracts import (
    AudioCapabilities,
    AudioGenerationResult,
    AudioPurpose,
    AudioRequestBase,
    ImageCapabilities,
    ImageGenerationRequest,
    ImageGenerationResult,
    MediaDimensions,
    MediaEncoding,
    MediaGenerationStrategy,
    MediaModality,
    PostProductionRequest,
    PostProductionResult,
    ProviderCapabilities,
    ResourceProfile,
    VideoCapabilities,
    VideoGenerationRequest,
    VideoPreflightResult,
    VideoGenerationResult,
    VisionCapabilities,
    VisionDecision,
    VisionInspectionProfile,
    VisionInspectionRequest,
    VisionInspectionResult,
    VisionScore,
)


ResultT = TypeVar("ResultT")


def normalize_media_provider_error(request_id: str, error: BaseException) -> ProviderResult:
    """Normalize provider failures without forwarding raw SDK/CUDA/HTTP exception text."""

    if isinstance(error, ProviderFailure):
        kind, retryable = error.error_type, error.retryable
    elif isinstance(error, TimeoutError):
        kind, retryable = ProviderErrorType.TIMEOUT, True
    elif isinstance(error, (ValueError, TypeError)):
        kind, retryable = ProviderErrorType.INVALID_REQUEST, False
    else:
        kind, retryable = ProviderErrorType.INTERNAL, False
    return ProviderResult(
        provider_request_id=request_id, success=False, retryable=retryable,
        error_type=kind, error_message=f"media_provider_{kind.value}",
        metadata=({"request_dispatched": error.request_dispatched,
                   "attempts": getattr(error, "attempts", [])}
                  if hasattr(error, "request_dispatched") else {}),
    )


@dataclass(frozen=True)
class BinaryPayload:
    """Process-local binary transfer; it is deliberately not JSON serializable."""

    artifact_id: str
    content: bytes
    mime_type: str
    extension: str
    purpose: str


@dataclass(frozen=True)
class ProviderMediaResponse(Generic[ResultT]):
    result: ResultT
    payloads: tuple[BinaryPayload, ...] = ()


@dataclass(frozen=True)
class MediaProviderProgress:
    status: JobStatus
    activity: str
    remote_event: str
    progress: float | None = None
    progress_is_determinate: bool = False
    node_id: str | None = None
    provider_execution_graph: dict | None = None
    provider_execution_update: dict | None = None


MediaProviderProgressCallback = Callable[[MediaProviderProgress], Awaitable[None] | None]


class MediaProvider(ABC):
    provider_id: str

    @abstractmethod
    async def health(self) -> bool: ...

    @abstractmethod
    async def capabilities(self) -> ProviderCapabilities: ...

    @abstractmethod
    async def status(self, request_id: str): ...

    @abstractmethod
    async def cancel(self, request_id: str) -> bool: ...


class ImageProvider(MediaProvider):
    @abstractmethod
    async def generate(self, request: ImageGenerationRequest) -> ProviderMediaResponse[ImageGenerationResult]: ...


class VideoProvider(MediaProvider):
    async def preflight(
        self,
        request: VideoGenerationRequest,
        *,
        strategy: MediaGenerationStrategy | None = None,
    ) -> VideoPreflightResult:
        """Default pass-through for providers without a workflow-specific preflight."""

        dimensions = MediaDimensions(
            width=request.width, height=request.height, aspect_ratio=request.aspect_ratio
        )
        return VideoPreflightResult(
            original_request=request.model_copy(deep=True),
            effective_request=request.model_copy(deep=True),
            compatible=True,
            requested_delivery_dimensions=dimensions,
            effective_generation_dimensions=dimensions.model_copy(deep=True),
        )

    @abstractmethod
    async def generate(
        self,
        request: VideoGenerationRequest,
        *,
        strategy: MediaGenerationStrategy | None = None,
        on_progress: MediaProviderProgressCallback | None = None,
    ) -> ProviderMediaResponse[VideoGenerationResult]: ...


class VisionProvider(MediaProvider):
    async def prepare_request(self, request: VisionInspectionRequest) -> VisionInspectionRequest:
        """Resolve decoder facts at the boundary before Core fingerprints evidence."""
        return request

    @abstractmethod
    async def inspect(self, request: VisionInspectionRequest) -> VisionInspectionResult: ...


class AudioProvider(MediaProvider):
    @abstractmethod
    async def generate(self, request: AudioRequestBase) -> ProviderMediaResponse[AudioGenerationResult]: ...


class PostProcessor(MediaProvider):
    @abstractmethod
    async def process(self, request: PostProductionRequest) -> ProviderMediaResponse[PostProductionResult]: ...


class _MockState:
    def __init__(self) -> None:
        self._results: dict[str, object] = {}
        self._cancelled: set[str] = set()

    async def health(self) -> bool:
        return True

    async def status(self, request_id: str):
        return self._results.get(request_id)

    async def cancel(self, request_id: str) -> bool:
        self._cancelled.add(request_id)
        return True

    def _save(self, request_id: str, result: ResultT) -> ResultT:
        if request_id in self._cancelled:
            raise RuntimeError("mock media request was cancelled")
        self._results[request_id] = result
        return result


def _resource_profiles() -> list[ResourceProfile]:
    return [
        ResourceProfile(resource_class=ResourceClass.LIGHT, supports_concurrency=True),
        ResourceProfile(resource_class=ResourceClass.MEDIUM, supports_concurrency=True),
        ResourceProfile(resource_class=ResourceClass.HEAVY, supports_concurrency=False),
        ResourceProfile(resource_class=ResourceClass.EXCLUSIVE, supports_concurrency=False,
                        requires_exclusive_runtime=True),
    ]


def _png(width: int = 64, height: int = 36) -> bytes:
    """Create a small valid RGB PNG using only the standard library."""

    width, height = max(1, min(width, 96)), max(1, min(height, 96))
    row = b"\x00" + b"\x24\x2b\x38" * width
    raw = row * height

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _wav(duration: float = 0.2, sample_rate: int = 8000) -> bytes:
    buffer = io.BytesIO()
    count = max(1, int(min(duration, 0.25) * sample_rate))
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        frames = b"".join(struct.pack("<h", int(500 * math.sin(index / 17))) for index in range(count))
        stream.writeframes(frames)
    return buffer.getvalue()


_MOCK_MP4 = base64.b64decode(
    "AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAMXbW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAAAMgAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAkF0cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAAMgAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAEAAAAAkAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAAADIAAAAAAABAAAAAAG5bWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAAAoAAAACABVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAABZG1pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAASRzdGJsAAAAwHN0c2QAAAAAAAAAAQAAALBhdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAAEAAJABIAAAASAAAAAAAAAABFUxhdmM2Mi4yOC4xMDAgbGlieDI2NAAAAAAAAAAAAAAAGP//AAAANmF2Y0MBZAAK/+EAGWdkAAqs2UR/nwEQAAADABAAAAMAoPEiWWABAAZo6+PLIsD9+PgAAAAAEHBhc3AAAAABAAAAAQAAABRidHJ0AAAAAAAAcZgAAAAAAAAAGHN0dHMAAAAAAAAAAQAAAAEAAAgAAAAAHHN0c2MAAAAAAAAAAQAAAAEAAAABAAAAAQAAABRzdHN6AAAAAAAAAtcAAAABAAAAFHN0Y28AAAAAAAAAAQAAA0cAAABidWR0YQAAAFptZXRhAAAAAAAAACFoZGxyAAAAAAAAAABtZGlyYXBwbAAAAAAAAAAAAAAAAC1pbHN0AAAAJal0b28AAAAdZGF0YQAAAAEAAAAATGF2ZjYyLjEyLjEwMAAAAAhmcmVlAAAC321kYXQAAAKtBgX//6ncRem95tlIt5Ys2CDZI+7veDI2NCAtIGNvcmUgMTY1IHIzMjIzIDA0ODBjYjAgLSBILjI2NC9NUEVH"
    "LTQgQVZDIGNvZGVjIC0gQ29weWxlZnQgMjAwMy0yMDI1IC0gaHR0cDovL3d3dy52aWRlb2xhbi5vcmcveDI2NC5odG1sIC0gb3B0aW9uczogY2FiYWM9MSByZWY9MyBkZWJsb2NrPTE6MDowIGFuYWx5c2U9MHgzOjB4MTEzIG1lPWhleCBzdWJtZT03IHBzeT0xIHBzeV9yZD0xLjAwOjAuMDAgbWl4ZWRfcmVmPTEgbWVfcmFuZ2U9MTYgY2hyb21hX21lPTEgdHJlbGxpcz0xIDh4OGRjdD0xIGNxbT0wIGRlYWR6b25lPTIxLDExIGZhc3RfcHNraXA9MSBjaHJvbWFfcXBfb2Zmc2V0PS0yIHRocmVhZHM9MSBsb29rYWhlYWRfdGhyZWFkcz0xIHNsaWNlZF90aHJlYWRzPTAgbnI9MCBkZWNpbWF0ZT0xIGludGVybGFjZWQ9MCBibHVyYXlfY29tcGF0PTAgY29uc3RyYWluZWRfaW50cmE9MCBiZnJhbWVzPTMgYl9weXJhbWlkPTIgYl9hZGFwdD0xIGJfYmlhcz0wIGRpcmVjdD0xIHdlaWdodGI9MSBvcGVuX2dvcD0wIHdlaWdodHA9MiBrZXlpbnQ9MjUwIGtleWludF9taW49NSBzY2VuZWN1dD00MCBpbnRyYV9yZWZyZXNoPTAgcmNfbG9va2FoZWFkPTQwIHJjPWNyZiBtYnRyZWU9MSBjcmY9MjMuMCBxY29tcD0wLjYwIHFwbWluPTAgcXBtYXg9NjkgcXBzdGVwPTQgaXBfcmF0aW89MS40MCBhcT0xOjEuMDAAgAAAACJliIQAP//+5nX4FNgJ4o6UXo2sVcp5wFs500OH1UoDGdzB"
)


class MockImageProvider(_MockState, ImageProvider):
    provider_id = "mock-image"

    def __init__(self) -> None:
        _MockState.__init__(self)

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id, kind=ProviderKind.IMAGE,
            modalities=[MediaModality.IMAGE], tasks=["image", "frame"],
            image=ImageCapabilities(text_to_image=True, image_edit=True, inpaint=True,
                                    outpaint=True, upscale=True, multi_reference=True,
                                    character_reference=True, max_width=8192, max_height=8192),
            resource_profiles=_resource_profiles(), quality_profiles=list(QualityProfile),
        )

    async def generate(self, request: ImageGenerationRequest) -> ProviderMediaResponse[ImageGenerationResult]:
        encoding = MediaEncoding(mime_type="image/png", format="png", codec="png")
        result = ImageGenerationResult(
            request_id=request.request_id, artifact_ids=[request.output_artifact_id],
            primary_artifact_id=request.output_artifact_id, provider_id=self.provider_id,
            seed=request.seed, dimensions=MediaDimensions(width=request.width, height=request.height,
                                                           aspect_ratio=request.aspect_ratio),
            encoding=encoding, provider_metadata={
                "mock": True,
                "test_asset": True,
                "reference_artifact_ids": [item.artifact_id for item in request.references],
            },
        )
        self._save(request.request_id, result)
        return ProviderMediaResponse(result, (BinaryPayload(request.output_artifact_id,
            _png(request.width, request.height), "image/png", "png", request.purpose.value),))


class MockVideoProvider(_MockState, VideoProvider):
    provider_id = "mock-video"

    def __init__(self) -> None:
        _MockState.__init__(self)

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id, kind=ProviderKind.VIDEO,
            modalities=[MediaModality.VIDEO], tasks=["video"],
            video=VideoCapabilities(text_to_video=True, image_to_video=True, video_to_video=True,
                video_extend=True, first_frame=True, last_frame=True,
                first_last_frame=True, multi_reference=True,
                camera_control=True, max_duration_seconds=120, max_width=7680,
                max_height=4320, supported_fps=[24, 25, 30, 60]),
            resource_profiles=_resource_profiles(), quality_profiles=list(QualityProfile),
        )

    async def generate(
        self,
        request: VideoGenerationRequest,
        *,
        strategy: MediaGenerationStrategy | None = None,
        on_progress: MediaProviderProgressCallback | None = None,
    ) -> ProviderMediaResponse[VideoGenerationResult]:
        encoding = MediaEncoding(mime_type="video/mp4", format="mp4", codec="h264")
        result = VideoGenerationResult(
            request_id=request.request_id, artifact_ids=[request.output_artifact_id],
            primary_artifact_id=request.output_artifact_id, provider_id=self.provider_id,
            duration_seconds=request.duration_seconds, fps=request.fps,
            dimensions=MediaDimensions(width=request.width, height=request.height,
                                       aspect_ratio=request.aspect_ratio),
            frame_count=max(1, round(request.duration_seconds * request.fps)), codec="h264",
            encoding=encoding, provider_metadata={
                "mock": True,
                "test_asset": True,
                "reference_artifact_ids": [item.artifact_id for item in request.references],
                "first_frame_artifact_id": request.first_frame.artifact_id if request.first_frame else None,
                "last_frame_artifact_id": request.last_frame.artifact_id if request.last_frame else None,
            },
        )
        self._save(request.request_id, result)
        return ProviderMediaResponse(result, (BinaryPayload(request.output_artifact_id,
            _MOCK_MP4, "video/mp4", "mp4", "shot_video"),))


class MockVisionProvider(_MockState, VisionProvider):
    provider_id = "mock-vision"

    def __init__(self, fail_once_shot_ids: set[str] | None = None) -> None:
        _MockState.__init__(self)
        self.fail_once_shot_ids = set(fail_once_shot_ids or set())
        self._failed: set[str] = set()

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id, kind=ProviderKind.VISION,
            modalities=[MediaModality.VISION], tasks=["inspect"],
            vision=VisionCapabilities(image=True, video=True, multi_image=True, temporal_reasoning=True),
            resource_profiles=_resource_profiles(), quality_profiles=list(QualityProfile),
        )

    async def inspect(self, request: VisionInspectionRequest) -> VisionInspectionResult:
        fail = bool(request.shot_id in self.fail_once_shot_ids and request.shot_id not in self._failed)
        if fail:
            self._failed.add(request.shot_id)
        scores = [VisionScore(profile=item, score=0.45 if fail else 0.94) for item in request.profiles]
        from movie_agent.media.contracts import MediaIssue, MediaIssueType, MediaRepairActionType
        from movie_agent.domain import IssueSeverity
        issues = ([MediaIssue(issue_type=MediaIssueType.ACTION_INCOMPLETE,
                    severity=IssueSeverity.MAJOR,
                    message="Mock subject does not complete the declared action.",
                    evidence=[f"artifact={request.video_artifact_id or request.image_artifact_id}"],
                    suggested_action=MediaRepairActionType.REWRITE_PROMPT)] if fail else [])
        result = VisionInspectionResult(
            request_id=request.request_id,
            target_artifact_id=request.video_artifact_id or request.image_artifact_id or "",
            scores=scores, issues=issues, evidence=["deterministic mock inspection"],
            decision=VisionDecision.REPAIR if fail else VisionDecision.PASS,
            summary="Intentional first-pass mock failure." if fail else "Mock inspection passed.",
            provider_id=self.provider_id, provider_metadata={"mock": True},
        )
        return self._save(request.request_id, result)


class MockAudioProvider(_MockState, AudioProvider):
    provider_id = "mock-audio"

    def __init__(self) -> None:
        _MockState.__init__(self)

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id, kind=ProviderKind.AUDIO,
            modalities=[MediaModality.AUDIO],
            tasks=["speech", "music", "sfx", "foley", "ambience", "mix"],
            audio=AudioCapabilities(tts=True, voice_clone=True, music=True, foley=True, sfx=True,
                                    ambience=True, audio_edit=True),
            resource_profiles=_resource_profiles(), quality_profiles=list(QualityProfile),
        )

    async def generate(self, request: AudioRequestBase) -> ProviderMediaResponse[AudioGenerationResult]:
        duration = request.duration_target_seconds or 1.0
        encoding = MediaEncoding(mime_type="audio/wav", format="wav", codec="pcm_s16le")
        result = AudioGenerationResult(
            request_id=request.request_id, artifact_ids=[request.output_artifact_id],
            primary_artifact_id=request.output_artifact_id, provider_id=self.provider_id,
            duration_seconds=duration, sample_rate=8000, channels=1, format="wav",
            loudness_lufs=-23.0, purpose=request.purpose, encoding=encoding,
            provider_metadata={"mock": True, "test_asset": True},
        )
        self._save(request.request_id, result)
        return ProviderMediaResponse(result, (BinaryPayload(request.output_artifact_id,
            _wav(duration), "audio/wav", "wav", request.purpose.value),))


class MockPostProcessor(_MockState, PostProcessor):
    provider_id = "mock-post"

    def __init__(self) -> None:
        _MockState.__init__(self)

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id, kind=ProviderKind.VIDEO,
            modalities=[MediaModality.POST], tasks=["post"],
            resource_profiles=_resource_profiles(), quality_profiles=list(QualityProfile),
        )

    async def process(self, request: PostProductionRequest) -> ProviderMediaResponse[PostProductionResult]:
        encoding = MediaEncoding(mime_type="video/mp4", format=request.output_format, codec=request.video_codec)
        result = PostProductionResult(
            request_id=request.request_id, artifact_ids=[request.output_artifact_id],
            primary_artifact_id=request.output_artifact_id, provider_id=self.provider_id,
            duration_seconds=request.timeline.duration_seconds,
            dimensions=MediaDimensions(width=request.width, height=request.height,
                                       aspect_ratio=f"{request.width}:{request.height}"),
            encoding=encoding, provider_metadata={"mock": True, "test_asset": True},
        )
        self._save(request.request_id, result)
        return ProviderMediaResponse(result, (BinaryPayload(request.output_artifact_id,
            _MOCK_MP4, "video/mp4", "mp4", "final_film"),))
