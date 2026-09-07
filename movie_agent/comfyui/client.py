"""ComfyUI HTTP/WebSocket transport kept outside media providers."""

from __future__ import annotations

import asyncio
import inspect
import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import httpx
from pydantic import Field

from movie_agent.domain import JobStatus, ProviderErrorType
from movie_agent.domain.base import ContractModel, JSONValue
from movie_agent.providers.base import ProviderFailure


def validate_comfyui_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(
            "ComfyUI endpoint must be a root HTTP(S) service URL without credentials, query, or fragment"
        )
    return value.rstrip("/")


class ComfyUIClient:
    """Typed operations for the local ComfyUI API contract."""

    def __init__(
        self,
        *,
        endpoint: str = "http://127.0.0.1:8188",
        timeout: float = 3600,
        websocket_timeout: float | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if timeout <= 0 or (websocket_timeout is not None and websocket_timeout <= 0):
            raise ValueError("ComfyUI timeouts must be positive")
        self.endpoint = validate_comfyui_endpoint(endpoint)
        self.timeout = timeout
        self.websocket_timeout = websocket_timeout or timeout
        self._http = http_client

    async def upload_image(
        self,
        *,
        filename: str,
        content: bytes,
        mime_type: str,
        subfolder: str = "movie-agent",
    ) -> "ComfyUIUploadedAsset":
        if not filename or "/" in filename or "\\" in filename:
            raise ValueError("ComfyUI upload filename must be a basename")
        payload = await self._json(
            "POST",
            "/upload/image",
            data={"type": "input", "subfolder": subfolder, "overwrite": "false"},
            files={"image": (filename, content, mime_type)},
        )
        try:
            return ComfyUIUploadedAsset.model_validate(payload)
        except ValueError as error:
            raise ProviderFailure(
                "ComfyUI returned an invalid upload identifier", ProviderErrorType.GENERATION_FAILED
            ) from error

    async def submit_prompt(
        self, prompt: dict[str, dict[str, JSONValue]], *, client_id: str
    ) -> "ComfyUISubmission":
        if not prompt or any(
            not isinstance(node, dict)
            or not isinstance(node.get("class_type"), str)
            or not isinstance(node.get("inputs"), dict)
            for node in prompt.values()
        ):
            raise ProviderFailure(
                "ComfyUI prompt is not API-format workflow JSON", ProviderErrorType.INVALID_REQUEST
            )
        payload = await self._json(
            "POST", "/prompt", json={"prompt": prompt, "client_id": client_id}
        )
        try:
            return ComfyUISubmission.model_validate(payload)
        except ValueError as error:
            raise ProviderFailure(
                "ComfyUI returned an invalid prompt submission", ProviderErrorType.GENERATION_FAILED
            ) from error

    async def execute_prompt(
        self,
        prompt: dict[str, dict[str, JSONValue]],
        *,
        execution_adapter: "ComfyUIExecutionEventAdapter | None" = None,
        on_update: "ComfyUIProgressCallback | None" = None,
        on_submitted: "ComfyUISubmissionCallback | None" = None,
    ) -> "ComfyUISubmission":
        adapter = execution_adapter or ComfyUIWebSocketExecutionEventAdapter()
        return await adapter.execute(
            self, prompt, client_id=uuid4().hex, on_update=on_update,
            on_submitted=on_submitted,
        )

    async def history(self, prompt_id: str) -> dict[str, Any]:
        payload = await self._json("GET", f"/history/{quote(prompt_id, safe='')}")
        item = payload.get(prompt_id)
        if not isinstance(item, dict):
            raise ProviderFailure("ComfyUI prompt history is unavailable", ProviderErrorType.GENERATION_FAILED)
        return item

    @staticmethod
    def output_files(
        history_item: dict[str, Any], *, node_id: str, history_key: str
    ) -> list["ComfyUIRemoteFile"]:
        outputs = history_item.get("outputs", {})
        node_output = outputs.get(node_id, {}) if isinstance(outputs, dict) else {}
        files = node_output.get(history_key, []) if isinstance(node_output, dict) else []
        if not isinstance(files, list):
            raise ProviderFailure("ComfyUI history output is invalid", ProviderErrorType.GENERATION_FAILED)
        try:
            return [ComfyUIRemoteFile.model_validate(item) for item in files]
        except ValueError as error:
            raise ProviderFailure("ComfyUI history file reference is invalid", ProviderErrorType.GENERATION_FAILED) from error

    async def view(self, remote_file: "ComfyUIRemoteFile") -> bytes:
        response = await self._request(
            "GET",
            "/view",
            params={
                "filename": remote_file.filename,
                "subfolder": remote_file.subfolder,
                "type": remote_file.type,
            },
        )
        return response.content

    async def job(self, job_id: str) -> dict[str, Any]:
        return await self._json("GET", f"/api/jobs/{quote(job_id, safe='')}")

    async def cancel_job(self, job_id: str) -> bool:
        payload = await self._json("POST", f"/api/jobs/{quote(job_id, safe='')}/cancel")
        return payload.get("cancelled") is True

    async def queue(self) -> dict[str, Any]:
        return await self._json("GET", "/queue")

    async def delete_queued(self, prompt_ids: list[str]) -> None:
        if not prompt_ids:
            return
        await self._request("POST", "/queue", json={"delete": prompt_ids})

    async def interrupt(self) -> None:
        await self._request("POST", "/interrupt")

    async def health(self) -> bool:
        try:
            await self.system_stats()
            return True
        except ProviderFailure:
            return False

    async def system_stats(self) -> dict[str, Any]:
        payload = await self._json("GET", "/system_stats")
        if not isinstance(payload.get("system"), dict):
            raise ProviderFailure("ComfyUI returned invalid system stats", ProviderErrorType.UNAVAILABLE)
        return payload

    async def object_info(self, node_classes: list[str] | None = None) -> dict[str, Any]:
        """Read all schemas, or only the explicitly requested workflow classes."""

        if node_classes is None:
            return await self._json("GET", "/object_info")
        classes = sorted(set(node_classes))
        payloads = await asyncio.gather(*(
            self._json("GET", f"/object_info/{quote(node_class, safe='')}")
            for node_class in classes
        ))
        merged: dict[str, Any] = {}
        for node_class, payload in zip(classes, payloads, strict=True):
            schema = payload.get(node_class)
            if isinstance(schema, dict):
                merged[node_class] = schema
        return merged

    async def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._request(method, path, **kwargs)
        try:
            payload = response.json()
        except ValueError as error:
            raise ProviderFailure(
                "ComfyUI returned non-JSON data", ProviderErrorType.GENERATION_FAILED
            ) from error
        if not isinstance(payload, dict):
            raise ProviderFailure(
                "ComfyUI returned an invalid JSON object", ProviderErrorType.GENERATION_FAILED
            )
        return payload

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        owns_client = self._http is None
        client = self._http or httpx.AsyncClient(base_url=self.endpoint, timeout=self.timeout)
        try:
            response = await client.request(method, path, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            return response
        except httpx.TimeoutException as error:
            raise ProviderFailure("ComfyUI request timed out", ProviderErrorType.TIMEOUT, retryable=True) from error
        except httpx.RequestError as error:
            raise ProviderFailure("PROVIDER_UNAVAILABLE: ComfyUI transport failed", ProviderErrorType.UNAVAILABLE, retryable=True) from error
        except httpx.HTTPStatusError as error:
            kind = ProviderErrorType.INVALID_REQUEST if error.response.status_code < 500 else ProviderErrorType.UNAVAILABLE
            raise ProviderFailure("ComfyUI request failed", kind, retryable=kind == ProviderErrorType.UNAVAILABLE) from error
        finally:
            if owns_client:
                await client.aclose()


ComfyUIProgressCallback = Callable[["ComfyUIExecutionUpdate"], Awaitable[None] | None]
ComfyUISubmissionCallback = Callable[["ComfyUISubmission"], Awaitable[None] | None]


class ComfyUIExecutionUpdate(ContractModel):
    status: JobStatus
    activity: str = Field(min_length=1)
    remote_event: str = Field(min_length=1)
    progress: float | None = Field(default=None, ge=0, le=1)
    progress_is_determinate: bool = False
    node_id: str | None = None
    cached_node_ids: list[str] = Field(default_factory=list)
    error: str | None = None


async def _notify(
    callback: ComfyUIProgressCallback | None, update: ComfyUIExecutionUpdate
) -> None:
    if callback is None:
        return
    result = callback(update)
    if inspect.isawaitable(result):
        await result


async def _notify_submitted(
    callback: ComfyUISubmissionCallback | None, submission: "ComfyUISubmission"
) -> None:
    if callback is None:
        return
    result = callback(submission)
    if inspect.isawaitable(result):
        await result


class ComfyUIExecutionEventAdapter(ABC):
    @abstractmethod
    async def execute(
        self,
        client: ComfyUIClient,
        prompt: dict[str, dict[str, JSONValue]],
        *,
        client_id: str,
        on_update: ComfyUIProgressCallback | None,
        on_submitted: ComfyUISubmissionCallback | None,
    ) -> "ComfyUISubmission": ...


class ComfyUIPollingExecutionEventAdapter(ComfyUIExecutionEventAdapter):
    """HTTP job adapter for tests and installations where WebSocket is unavailable."""

    def __init__(self, *, poll_interval: float = 0.25) -> None:
        if poll_interval < 0:
            raise ValueError("ComfyUI poll interval cannot be negative")
        self.poll_interval = poll_interval

    async def execute(
        self,
        client: ComfyUIClient,
        prompt: dict[str, dict[str, JSONValue]],
        *,
        client_id: str,
        on_update: ComfyUIProgressCallback | None,
        on_submitted: ComfyUISubmissionCallback | None,
    ) -> "ComfyUISubmission":
        submission = await client.submit_prompt(prompt, client_id=client_id)
        await _notify_submitted(on_submitted, submission)
        last_status: JobStatus | None = None
        while True:
            job = await client.job(submission.prompt_id)
            remote_status = str(job.get("status", "")).lower()
            status = {
                "pending": JobStatus.QUEUED,
                "in_progress": JobStatus.RUNNING,
                "completed": JobStatus.SUCCEEDED,
                "failed": JobStatus.FAILED,
                "cancelled": JobStatus.CANCELLED,
            }.get(remote_status)
            if status is None:
                raise ProviderFailure("ComfyUI returned an unknown job status", ProviderErrorType.GENERATION_FAILED)
            if status != last_status:
                await _notify(on_update, ComfyUIExecutionUpdate(
                    status=status,
                    activity=f"ComfyUI job {remote_status}",
                    remote_event="job_status",
                ))
                last_status = status
            if status == JobStatus.SUCCEEDED:
                return submission
            if status == JobStatus.CANCELLED:
                raise ProviderFailure("ComfyUI execution was interrupted", ProviderErrorType.CANCELLED)
            if status == JobStatus.FAILED:
                raise ProviderFailure("ComfyUI execution failed", ProviderErrorType.GENERATION_FAILED)
            await asyncio.sleep(self.poll_interval)


class ComfyUIWebSocketExecutionEventAdapter(ComfyUIExecutionEventAdapter):
    """Submit while connected to `/ws` and map real ComfyUI execution events."""

    async def execute(
        self,
        client: ComfyUIClient,
        prompt: dict[str, dict[str, JSONValue]],
        *,
        client_id: str,
        on_update: ComfyUIProgressCallback | None,
        on_submitted: ComfyUISubmissionCallback | None,
    ) -> "ComfyUISubmission":
        try:
            try:
                from websockets.asyncio.client import connect
            except ImportError:
                from websockets import connect  # type: ignore[attr-defined,no-redef]
        except ImportError as error:
            raise ProviderFailure(
                "ComfyUI WebSocket support is not installed", ProviderErrorType.UNAVAILABLE
            ) from error

        parsed = urlsplit(client.endpoint)
        ws_url = urlunsplit((
            "wss" if parsed.scheme == "https" else "ws",
            parsed.netloc,
            "/ws",
            urlencode({"clientId": client_id}),
            "",
        ))
        submission: ComfyUISubmission | None = None
        try:
            async with connect(
                ws_url,
                open_timeout=client.websocket_timeout,
                close_timeout=min(10, client.websocket_timeout),
                max_size=4 * 1024 * 1024,
            ) as websocket:
                submission = await client.submit_prompt(prompt, client_id=client_id)
                await _notify_submitted(on_submitted, submission)
                await _notify(on_update, ComfyUIExecutionUpdate(
                    status=JobStatus.QUEUED,
                    activity="ComfyUI prompt queued",
                    remote_event="prompt_submitted",
                ))
                async with asyncio.timeout(client.websocket_timeout):
                    async for message in websocket:
                        if not isinstance(message, str):
                            continue
                        payload = json.loads(message)
                        event = str(payload.get("type", ""))
                        data = payload.get("data", {})
                        if not isinstance(data, dict):
                            continue
                        event_prompt_id = data.get("prompt_id")
                        if event_prompt_id not in {None, submission.prompt_id}:
                            continue
                        update = self._update(event, data)
                        if update is not None:
                            await _notify(on_update, update)
                        if event == "execution_success":
                            return submission
                        if event == "execution_interrupted":
                            raise ProviderFailure("ComfyUI execution was interrupted", ProviderErrorType.CANCELLED)
                        if event == "execution_error":
                            raise ProviderFailure("ComfyUI execution failed", ProviderErrorType.GENERATION_FAILED)
        except ProviderFailure:
            raise
        except TimeoutError as error:
            raise ProviderFailure("ComfyUI WebSocket timed out", ProviderErrorType.TIMEOUT, retryable=True) from error
        except Exception as error:
            raise ProviderFailure("PROVIDER_UNAVAILABLE: ComfyUI WebSocket failed", ProviderErrorType.UNAVAILABLE, retryable=True) from error
        raise ProviderFailure("ComfyUI WebSocket closed before completion", ProviderErrorType.GENERATION_FAILED)

    @staticmethod
    def _update(event: str, data: dict[str, Any]) -> ComfyUIExecutionUpdate | None:
        if event in {"execution_start", "executing"}:
            return ComfyUIExecutionUpdate(
                status=JobStatus.RUNNING,
                activity=(f"ComfyUI executing node {data['node']}" if data.get("node") else "ComfyUI executing"),
                remote_event=event,
                node_id=str(data["node"]) if data.get("node") is not None else None,
            )
        if event == "execution_cached":
            nodes = data.get("nodes", [])
            cached = [str(node) for node in nodes] if isinstance(nodes, list) else []
            return ComfyUIExecutionUpdate(
                status=JobStatus.RUNNING,
                activity=f"ComfyUI reused {len(cached)} cached nodes",
                remote_event=event,
                cached_node_ids=cached,
            )
        if event == "progress_state":
            nodes = data.get("nodes", {})
            active = [item for item in nodes.values() if isinstance(item, dict)] if isinstance(nodes, dict) else []
            node = active[-1] if active else {}
            value, maximum = node.get("value"), node.get("max")
            determinate = isinstance(value, (int, float)) and isinstance(maximum, (int, float)) and maximum > 0
            return ComfyUIExecutionUpdate(
                status=JobStatus.RUNNING,
                activity=(f"ComfyUI sampling node {node.get('node_id')}" if node.get("node_id") else "ComfyUI sampling"),
                remote_event=event,
                progress=max(0.0, min(1.0, float(value) / float(maximum))) if determinate else None,
                progress_is_determinate=determinate,
                node_id=str(node["node_id"]) if node.get("node_id") is not None else None,
            )
        if event == "execution_success":
            return ComfyUIExecutionUpdate(
                status=JobStatus.SUCCEEDED, activity="ComfyUI execution succeeded", remote_event=event
            )
        if event == "execution_interrupted":
            return ComfyUIExecutionUpdate(
                status=JobStatus.CANCELLED, activity="ComfyUI execution interrupted", remote_event=event
            )
        if event == "execution_error":
            return ComfyUIExecutionUpdate(
                status=JobStatus.FAILED,
                activity="ComfyUI execution failed",
                remote_event=event,
                node_id=str(data["node_id"]) if data.get("node_id") is not None else None,
                error="ComfyUI node execution failed",
            )
        return None


class ComfyUIUploadedAsset(ContractModel):
    name: str = Field(min_length=1)
    subfolder: str = ""
    type: str = "input"

    @property
    def input_name(self) -> str:
        return f"{self.subfolder.rstrip('/')}/{self.name}" if self.subfolder else self.name


class ComfyUISubmission(ContractModel):
    prompt_id: str = Field(min_length=1)
    number: int | None = None
    node_errors: dict[str, JSONValue] = Field(default_factory=dict)


class ComfyUIRemoteFile(ContractModel):
    filename: str = Field(min_length=1)
    subfolder: str = ""
    type: str = "output"
