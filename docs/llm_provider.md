# OpenAI-Compatible LLM Provider

`OpenAICompatibleLLMProvider` implements the existing `LLMProvider` ABC. It knows only serving requests, schemas and completion results. There is no Director/Screenwriter/Shot-specific transport code.

Configuration is loaded by `LLMConfig.from_env()`. Environment variables override `.env`; importing domain modules does not load configuration or contact a server. The API key uses `SecretStr`, is sent only in the authorization header, and is never placed in events, context, provenance or checkpoints.

| Variable | Meaning |
|---|---|
| MOVIE_AGENT_LLM_BASE_URL | OpenAI-compatible base URL including `/v1` |
| MOVIE_AGENT_LLM_MODEL | Served model alias |
| MOVIE_AGENT_LLM_API_KEY | Serving authorization key |
| MOVIE_AGENT_LLM_TIMEOUT | Request timeout in seconds; default 180 |
| MOVIE_AGENT_LLM_MAX_TOKENS | Serving output ceiling; default 16000 |
| MOVIE_AGENT_LLM_ENABLE_THINKING | Defaults false |

The local development example is `http://localhost:8000/v1`, model alias `movie-agent-brain`, mapped through an already-running SSH tunnel. Spark and its serving deployment are external to Movie Agent Core. The application neither connects over SSH nor manages the server.

`health()` checks `/models` and verifies the configured alias. `submit()` awaits `/chat/completions` through httpx, with split timeouts, `response_format.type=json_schema`, `stream=false`, and `chat_template_kwargs.enable_thinking=false` by default. Named role output budgets cap output length independently of the serving ceiling.

The existing GenerationRequest is reused with the additive `STRUCTURED_TEXT` strategy and typed `CompletionOptions` inside its parameters envelope. ProviderResult metadata contains final content, selected serving model, remote request ID, integer token usage, finish reason, local request trace and latency. Reasoning fields and raw server error bodies are deliberately excluded.

Connect/pool timeouts normalize to TIMEOUT; connection failures and HTTP 5xx to UNAVAILABLE; 429 to RESOURCE_EXHAUSTED; other 4xx to INVALID_REQUEST. Read/write timeouts and interrupted response transport normalize to REMOTE_COMPLETION_UNCERTAIN, with retryable=false: the server may still be computing. No hidden HTTP retry loop multiplies the role repair budget. A transport failure fails the node and remains visible for an explicit resume. Connection failures have a distinct `connect_failure` outcome.

Completed successful/non-retryable requests are cached for idempotent resubmission. Retryable failures remain visible through `status()` but allow a new explicit `submit()` with the same request ID, so restoring connectivity does not require restarting the API process.

An uncertain completion is cached until an explicit resumed role request sets `retry_uncertain`. RoleRunner persists this failure separately from output-validation attempts, exits immediately, and only sets that flag when a subsequent run resumes the saved uncertain result. The flag does not introduce automatic retries or reset the two-repair budget. Remote exactly-once execution cannot be guaranteed by this synchronous endpoint.

## Role inference policy

`RoleInferencePolicy` lives outside Domain and carries connect/read/write/pool timeouts, max_output_tokens, thinking, and structured_output. `MOVIE_AGENT_LLM_TIMEOUT` remains the default read timeout (180s unless configured otherwise). RoleDefinition can override it. Connect/write/pool defaults are 10/30/10s, independent of read timeout. Structured-text requests reject disabling structured output.

Cinematographer overrides read timeout to 360s and disables thinking. Its output ceiling is min(global ceiling, role ceiling 12000, 2000 + 5000 × scene max shots). A 6-second scene recommends one shot and allows at most two: the resulting 12000-token ceiling leaves about 67s beyond output generation at the observed 41 tokens/s within the 360s read window. This is a sizing estimate, not a guaranteed latency bound. Other five roles retain their existing output ceilings and inherited read timeout.

Every role request records role/scene IDs, context/prompt characters, schema characters, input token estimate, effective output/read budgets, start/end times, latency, provider outcome, actual prompt/completion usage when available, and validation outcome. Input estimates use ceil((prompt + system + schema characters)/4), not the model tokenizer. Metrics persist in RoleResult and durable lifecycle events; no reasoning or raw HTTP bodies are stored.

`status()` reports only completions observed by this local provider instance, returning null for unknown or in-flight requests. `cancel()` aborts local waiting/I/O when possible; it makes no claim that remote GPU inference stopped. Consequently capability metadata advertises no remote cancellation guarantee. API job cancellation is cooperative and prevents subsequent state commit.

Ordinary tests use httpx fake transport and deterministic role fixtures. Real endpoint tests are opt-in via `MOVIE_AGENT_RUN_INTEGRATION=1`; when opted in, unavailable serving is a test failure rather than a mock fallback.
