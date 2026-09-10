"""Allow-listed Spark control plane. Providers never receive Docker/SSH commands."""

import asyncio
import json
import re
from time import perf_counter
from pathlib import Path

import httpx

from movie_agent.domain import ProviderErrorType
from movie_agent.media import ModelServiceStatus
from movie_agent.providers.base import ProviderFailure
from .contracts import ModelService
from .resources import ModelResidency, ResourceSnapshot, ResourceRuntimeSettings


# Verified with docker inspect; obsolete/failed experiment containers are excluded.
CONTAINERS = {
    "qwen": "movie-agent-llm",
    "flux": "movie-agent-flux-direct",
    "kontext": "movie-agent-kontext",
    "comfyui": "movie-agent-comfyui",
    "vlm": "movie-agent-vlm",
    "tts": "movie-agent-tts",
    "music": "movie-agent-music",
}


class SparkDockerServiceController:
    def __init__(self, settings: ResourceRuntimeSettings, *, runner=None):
        self.settings = settings
        self.runner = runner or self._run
        self.stop_journal = Path(settings.state_path).with_suffix(".lifecycle.json") if runner is None else None
        self.controlled_stops = (json.loads(self.stop_journal.read_text(encoding="utf-8"))
                                 if self.stop_journal and self.stop_journal.exists() else {})
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", settings.ssh_host):
            raise ValueError("invalid Spark host")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", settings.ssh_user):
            raise ValueError("invalid Spark user")

    async def _run(self, command: str, timeout: float) -> str:
        args = ["ssh", "-p", str(self.settings.ssh_port), "-i", self.settings.ssh_key_path,
                "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=10",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
                f"{self.settings.ssh_user}@{self.settings.ssh_host}", command]
        process = await asyncio.create_subprocess_exec(*args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise ProviderFailure(f"Spark control command failed (exit {process.returncode})", ProviderErrorType.UNAVAILABLE)
        return stdout.decode("utf-8")

    def _container(self, service_id):
        if service_id not in CONTAINERS:
            raise ValueError("service is not allow-listed")
        return CONTAINERS[service_id]

    async def inspect(self, service_id):
        name = self._container(service_id)
        output = await self.runner(f"docker inspect --format '{{{{json .State}}}}' {name}", 20)
        return json.loads(output)

    async def action(self, service_id, action):
        name = self._container(service_id)
        if action not in {"start", "stop"}:
            raise ValueError("unsupported lifecycle action")
        state = await self.inspect(service_id)
        if bool(state.get("Running")) == (action == "start"):
            return state
        timeout = (self.settings.service_start_timeout if action == "start"
                   else self.settings.service_stop_timeout)
        command = f"docker start {name}" if action == "start" else f"docker stop --time 30 {name}"
        try:
            await self.runner(command, timeout)
        except TimeoutError as error:
            raise ProviderFailure(f"service {action} timeout", ProviderErrorType.TIMEOUT) from error
        state = await self.inspect(service_id)
        if bool(state.get("Running")) != (action == "start"):
            raise ProviderFailure(f"service {action} verification failed", ProviderErrorType.MODEL_NOT_READY)
        if action == "stop" and not state.get("OOMKilled") and state.get("FinishedAt"):
            self.record_controlled_stop(service_id, state)
        return state

    def record_controlled_stop(self, service_id, state):
        self._container(service_id)
        self.controlled_stops[service_id] = {"finished_at": state["FinishedAt"],
            "exit_code": state.get("ExitCode"), "reason": "verified_controlled_stop",
            "oom_killed": bool(state.get("OOMKilled"))}
        if self.stop_journal:
            self.stop_journal.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.stop_journal.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.controlled_stops), encoding="utf-8")
            temporary.replace(self.stop_journal)

    def is_controlled_stop(self, service_id, state):
        previous = self.controlled_stops.get(service_id, {})
        return (not state.get("OOMKilled") and bool(previous.get("finished_at"))
                and previous["finished_at"] == state.get("FinishedAt"))

    async def memory(self):
        # No shell fragments from Domain. CUDA total/free is diagnostic only on GB10.
        raw = await self.runner("cat /proc/meminfo", 20)
        values = {line.split(":", 1)[0]: int(line.split()[1]) * 1024
                  for line in raw.splitlines() if line.startswith(("MemTotal:", "MemAvailable:", "MemFree:"))}
        if not 0 <= values.get("MemAvailable", -1) <= values.get("MemTotal", 0):
            raise ProviderFailure("invalid unified memory telemetry", ProviderErrorType.UNAVAILABLE)
        return values


class SparkResourceTelemetry:
    def __init__(self, controller):
        self.controller = controller

    async def snapshot(self):
        values = await self.controller.memory()
        return ResourceSnapshot(total_unified_memory_bytes=values["MemTotal"],
            available_unified_memory_bytes=values["MemAvailable"],
            diagnostics={"pool": "GB10 unified host memory", "source": "/proc/meminfo",
                         "free_unified_memory_bytes": values.get("MemFree"),
                         "cuda_is_same_pool": True})


class SparkDockerModelService(ModelService):
    def __init__(self, descriptor, controller, *, health_path, idle_path, kind, headers=None):
        self.descriptor = descriptor
        self.controller = controller
        self.health_path, self.idle_path, self.kind = health_path, idle_path, kind
        self.headers = headers or {}

    async def _get(self, path, *, allow_unready=False):
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            response = await client.get(self.descriptor.endpoint + path, headers=self.headers)
            if not (allow_unready and response.status_code == 503):
                response.raise_for_status()
            return response

    async def health(self):
        try:
            response = await self._get(self.health_path, allow_unready=self.kind == 'flux')
            if self.kind == 'flux':
                payload = response.json()
                self.descriptor.metadata['reported_state'] = payload.get('state')
                self.descriptor.metadata['load_error_code'] = payload.get('load_error_code')
            if self.kind in {"flux", "tts", "kontext"}:
                return response.json().get("ready") is True
            if self.kind == 'music':
                data = response.json().get('data', {})
                if not isinstance(data, dict) or not isinstance(data.get('models'), list):
                    return False
                return any(isinstance(m, dict) and m.get('name') == self.descriptor.runtime_profile.model_profile_id
                           and m.get('is_loaded') is True for m in data['models'])
            if self.kind == "comfyui":
                return isinstance(response.json().get("system"), dict)
            model_id = self.descriptor.metadata.get("served_model", self.descriptor.runtime_profile.model_profile_id)
            return any(item.get("id") == model_id for item in response.json().get("data", []))
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return False

    async def idle(self):
        state = await self.controller.inspect(self.descriptor.service_id)
        if not state.get("Running"):
            return True
        try:
            response = await self._get(self.idle_path, allow_unready=self.kind == 'flux')
            if self.kind == "comfyui":
                data = response.json()
                return data.get("queue_running") == [] and data.get("queue_pending") == []
            if self.kind == 'kontext':
                data = response.json()
                return data.get('ready') is True and data.get('busy') is False
            if self.kind in {"flux", "tts"}:
                data = response.json()
                # Legacy services without the activity contract are not safely evictable.
                return data.get("state") == "ready" or (self.kind == 'flux'
                    and data.get('state') == 'failed' and bool(data.get('load_error_code')))
            if self.kind == 'music':
                data = response.json().get('data', {})
                return data.get('queue_size') == 0 and data.get('jobs', {}).get('running') == 0 and data.get('jobs', {}).get('queued') == 0
            running = re.findall(r'^vllm:num_requests_(?:running|waiting)(?:\{[^\n]*\})?\s+([0-9.eE+-]+)',
                                 response.text, flags=re.MULTILINE)
            return len(running) >= 2 and all(float(value) == 0 for value in running)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return False

    async def status(self):
        state = await self.controller.inspect(self.descriptor.service_id)
        if not state.get("Running"):
            self.descriptor.residency = ModelResidency.UNLOADED
            controlled = self.controller.is_controlled_stop(self.descriptor.service_id, state)
            status = ModelServiceStatus.FAILED if self._oom(state, controlled_stop=controlled) else ModelServiceStatus.STOPPED
            if controlled:
                self.descriptor.metadata["last_controlled_stop"] = self.controller.controlled_stops[self.descriptor.service_id]
        elif self.descriptor.status in {ModelServiceStatus.BUSY, ModelServiceStatus.DRAINING}:
            status = self.descriptor.status
        else:
            status = ModelServiceStatus.READY if await self.health() else ModelServiceStatus.WARMING
            if self.kind == 'flux' and self.descriptor.metadata.get('reported_state') == 'failed':
                status = ModelServiceStatus.FAILED
        self.descriptor.status = status
        return status

    @staticmethod
    def _oom(state, *, controlled_stop=False):
        return state.get("OOMKilled") is True or (not controlled_stop and not state.get("Running") and state.get("ExitCode") == 137)

    async def oom_killed(self):
        state = await self.controller.inspect(self.descriptor.service_id)
        return self._oom(state, controlled_stop=self.controller.is_controlled_stop(self.descriptor.service_id, state))

    async def remote_terminal(self, prompt_id):
        if self.kind != "comfyui" or not re.fullmatch(r"[a-zA-Z0-9-]+", prompt_id):
            return False
        try:
            data = (await self._get(f"/history/{prompt_id}")).json().get(prompt_id, {})
            status = data.get("status", {})
            return status.get("completed") is True or status.get("status_str") == "error"
        except (httpx.HTTPError, ValueError, TypeError):
            return False

    async def start(self):
        if self.kind == 'flux' and self.descriptor.metadata.get('reported_state') == 'failed':
            await self.stop()  # Failed loader has no accepted generation; restart only after verified idle.
        self.descriptor.status = ModelServiceStatus.STARTING
        started = perf_counter()
        await self.controller.action(self.descriptor.service_id, "start")
        self.startup_seconds = perf_counter() - started
        warming = perf_counter()
        self.descriptor.status = ModelServiceStatus.WARMING
        # FLUX and Qwen health includes initial model loading; ComfyUI does not.
        while not await self.health():
            if self.kind == 'flux' and self.descriptor.metadata.get('reported_state') == 'failed':
                code = self.descriptor.metadata.get('load_error_code')
                raise ProviderFailure('FLUX loader failed: '+str(code),
                    ProviderErrorType.RESOURCE_EXHAUSTED_OOM if code == 'RESOURCE_EXHAUSTED'
                    else ProviderErrorType.MODEL_NOT_READY)
            if await self.oom_killed():
                raise ProviderFailure("RESOURCE_EXHAUSTED_OOM", ProviderErrorType.RESOURCE_EXHAUSTED_OOM)
            if not (await self.controller.inspect(self.descriptor.service_id)).get("Running"):
                raise ProviderFailure("service exited before readiness", ProviderErrorType.MODEL_NOT_READY)
            await asyncio.sleep(2)
        self.descriptor.status = ModelServiceStatus.READY
        self.warmup_seconds = perf_counter() - warming
        self.descriptor.residency = (ModelResidency.UNKNOWN if self.kind == "comfyui"
                                     else ModelResidency.RESIDENT)
        return self.descriptor.status

    async def stop(self):
        if not await self.idle():
            raise ProviderFailure("service activity is busy or unknown", ProviderErrorType.RESOURCE_EXHAUSTED)
        self.descriptor.status = ModelServiceStatus.STOPPING
        await self.controller.action(self.descriptor.service_id, "stop")
        self.descriptor.status = ModelServiceStatus.STOPPED
        self.descriptor.residency = ModelResidency.UNLOADED
        return self.descriptor.status
