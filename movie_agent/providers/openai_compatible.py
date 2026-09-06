"""OpenAI-compatible transport adapter, independent of creative roles."""

from __future__ import annotations

import asyncio
from time import perf_counter

import httpx
from pydantic import Field

from movie_agent.config import LLMConfig
from movie_agent.domain import (
    ContractModel, JSONValue, ProviderCapability, ProviderErrorType,
    ProviderKind, ProviderRequest, ProviderResult, QualityProfile, ResourceClass,
    GenerationStrategyType,
)
from movie_agent.providers.base import LLMProvider
from .inference import RoleInferencePolicy
from .streaming import collect_completion


class CompletionOptions(ContractModel):
    """Typed options inside the existing GenerationRequest parameter envelope."""

    system_prompt: str
    response_format: dict[str, JSONValue]
    max_tokens: int = Field(default=16000, ge=128, le=32768)
    inference_policy: RoleInferencePolicy = Field(default_factory=RoleInferencePolicy)
    retry_uncertain: bool = False


class OpenAICompatibleLLMProvider(LLMProvider):
    """Synchronous remote completions awaited locally; cancellation aborts local I/O only."""

    def __init__(self, config: LLMConfig, *, client: httpx.AsyncClient | None = None,
                 provider_id: str = "reasoning") -> None:
        self.provider_id = provider_id
        self.config = config
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(
            connect=10, read=config.timeout, write=30, pool=10), trust_env=False)
        self._owns_client = client is None
        self._results: dict[str, ProviderResult] = {}
        self._active: dict[str, asyncio.Task] = {}

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.api_key.get_secret_value()}"}

    async def health(self) -> bool:
        try:
            response = await self._client.get(self.config.base_url + "/models", headers=self._headers(),
                                              timeout=min(self.config.timeout, 8))
            response.raise_for_status()
            return any(item.get("id") == self.config.model for item in response.json().get("data", []))
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return False

    async def capabilities(self) -> ProviderCapability:
        return ProviderCapability(provider_id=self.provider_id, kind=ProviderKind.LLM,
            tasks=["structured_text"], generation_strategies=[GenerationStrategyType.STRUCTURED_TEXT],
            resource_classes=[ResourceClass.LIGHT], quality_profiles=list(QualityProfile),
            supports_cancellation=False)

    async def submit(self, request: ProviderRequest) -> ProviderResult:
        rid = request.provider_request_id
        previous = self._results.get(rid)
        # An explicit resume may retry transport failures after connectivity recovers.
        # Keep successful/non-retryable results idempotent; never retry automatically.
        explicit_uncertain_retry = (previous is not None
            and previous.error_type == ProviderErrorType.REMOTE_COMPLETION_UNCERTAIN
            and request.generation_request.parameters.get("retry_uncertain") is True)
        if previous is not None and not previous.retryable and not explicit_uncertain_retry:
            return previous.model_copy(deep=True)
        if rid in self._active:
            return await asyncio.shield(self._active[rid])
        task = asyncio.create_task(self._complete(request))
        self._active[rid] = task
        try:
            result = await task
        except asyncio.CancelledError:
            result = ProviderResult(provider_request_id=rid, success=False,
                error_type=ProviderErrorType.CANCELLED, error_message="Local completion request cancelled")
            self._results[rid] = result
            raise
        finally:
            self._active.pop(rid, None)
        self._results[rid] = result
        return result.model_copy(deep=True)

    async def _complete(self, request: ProviderRequest) -> ProviderResult:
        started = perf_counter()
        rid = request.provider_request_id
        failure_phase = "response"
        try:
            if request.provider_id != self.provider_id or request.generation_request.task != "structured_text":
                raise ValueError("unsupported provider request")
            options = CompletionOptions.model_validate(request.generation_request.parameters)
            policy = options.inference_policy.resolve(self.inference_defaults(), max_tokens=options.max_tokens)
            if not policy.structured_output:
                raise ValueError("structured_text requires schema-constrained output")
            body = {
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": options.system_prompt},
                    {"role": "user", "content": request.generation_request.prompt_package.positive_prompt},
                ],
                "response_format": options.response_format,
                "max_tokens": policy.max_output_tokens,
                "temperature": 0.2,
                "chat_template_kwargs": {"enable_thinking": policy.thinking},
                "stream": policy.stream,
            }
            # Independent wall-clock ceiling. Streaming read timeout measures inactivity.
            async with asyncio.timeout(policy.total_timeout or (policy.read_timeout + 50)):
                data = await self._completion_data(body, rid, policy)
            choice = data["choices"][0]
            content = choice["message"].get("content")
            # Only final content is retained. Reasoning and raw remote bodies never persist.
            metadata = {"content": content if isinstance(content, str) else "",
                "served_model": self.config.model, "remote_request_id": data.get("id"),
                "usage": {k: v for k, v in data.get("usage", {}).items()
                          if k in {"prompt_tokens", "completion_tokens", "total_tokens"}},
                "finish_reason": choice.get("finish_reason"), "request_trace": rid}
            return ProviderResult(provider_request_id=rid, success=True, metadata=metadata,
                latency_seconds=perf_counter() - started)
        except TimeoutError:
            failure_phase = "total_budget"
            error, retry, message = (ProviderErrorType.REMOTE_COMPLETION_UNCERTAIN, False,
                "Total inference budget exhausted; remote completion uncertain. Explicit resume required")
        except httpx.ReadTimeout:
            failure_phase = "read"
            error, retry, message = (ProviderErrorType.REMOTE_COMPLETION_UNCERTAIN, False,
                "Read timeout after submission; remote completion uncertain. Explicit resume required")
        except httpx.ConnectTimeout:
            failure_phase = "connect"
            error, retry, message = ProviderErrorType.TIMEOUT, True, "Provider connection timed out"
        except httpx.PoolTimeout:
            failure_phase = "pool"
            error, retry, message = ProviderErrorType.TIMEOUT, True, "Completion request timed out"
        except httpx.WriteTimeout:
            failure_phase = "write"
            error, retry, message = (ProviderErrorType.REMOTE_COMPLETION_UNCERTAIN, False,
                "Write timeout; remote submission uncertain. Explicit resume required")
        except httpx.TimeoutException:
            error, retry, message = ProviderErrorType.TIMEOUT, True, "Completion request timed out"
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            error = (ProviderErrorType.RESOURCE_EXHAUSTED if status == 429 else
                     ProviderErrorType.UNAVAILABLE if status >= 500 else ProviderErrorType.INVALID_REQUEST)
            retry, message = status >= 500 or status in {408, 429}, f"Provider HTTP status {status}"
        except httpx.ConnectError:
            failure_phase = "connect"
            error, retry, message = ProviderErrorType.UNAVAILABLE, True, "Provider connection unavailable"
        except httpx.RequestError:
            error, retry, message = (ProviderErrorType.REMOTE_COMPLETION_UNCERTAIN, False,
                "Response transport failed; remote completion uncertain. Explicit resume required")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            error, retry, message = ProviderErrorType.INVALID_REQUEST, False, "Invalid completion request or response"
        return ProviderResult(provider_request_id=rid, success=False, retryable=retry,
            error_type=error, error_message=message, latency_seconds=perf_counter() - started,
            metadata={"failure_phase": failure_phase, "provider_outcome":
                "connect_failure" if failure_phase == "connect" else error.value})

    async def _completion_data(self, body, rid, policy):
        timeout = httpx.Timeout(connect=policy.connect_timeout,
            read=policy.inactivity_timeout if policy.stream else policy.read_timeout,
            write=policy.write_timeout, pool=policy.pool_timeout)
        headers = {**self._headers(), "X-Request-ID": rid}
        url = self.config.base_url + "/chat/completions"
        if not policy.stream:
            response = await self._client.post(url, json=body, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.json()
        body['stream_options'] = {'include_usage': True}
        async with self._client.stream('POST', url, json=body, headers=headers, timeout=timeout) as response:
            response.raise_for_status()
            return await collect_completion(response, inactivity=policy.inactivity_timeout,
                                            max_chars=policy.max_output_tokens * 64)

    async def status(self, provider_request_id: str) -> ProviderResult | None:
        result = self._results.get(provider_request_id)
        return result.model_copy(deep=True) if result else None

    def inference_defaults(self) -> RoleInferencePolicy:
        return RoleInferencePolicy(read_timeout=self.config.timeout, max_output_tokens=self.config.max_tokens,
                                   thinking=self.config.enable_thinking)

    async def cancel(self, provider_request_id: str) -> bool:
        task = self._active.get(provider_request_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
