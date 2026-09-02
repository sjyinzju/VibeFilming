# Future Provider Integration

## Adapter rule

Real integrations implement one of `LLMProvider`, `VisionProvider`, `ImageProvider`, `VideoProvider`, or `AudioProvider`. Business services must never call provider endpoints or SDKs directly.

Each adapter implements:

```text
health()        availability signal
capabilities()  task/strategy/input/resource/quality declaration
submit()        normalized asynchronous request submission
status()        normalized status/result lookup
cancel()        cooperative cancellation
```

## Integration sequence

1. Declare capabilities accurately, including accepted references/anchors, supported strategies, duration limits, quality profiles, seed support, and resource classes.
2. Add a prompt compiler only if the provider requires special syntax. Keep it outside Cinematic IR.
3. Translate `GenerationRequest` into the SDK/API request inside the adapter.
4. Store external request IDs privately or in provider-result metadata.
5. Normalize all outcomes into `ProviderResult`.
6. Register output through `ArtifactStore`; never return an untracked file path to business logic.
7. Record provider, prompt, inputs, strategy, parameters/seed, and retry/repair IDs in provenance.
8. Add contract, normalization, cancellation, retryability, and idempotency tests with a fake transport before enabling real calls.

## Error normalization

Adapters map SDK exceptions and remote states to timeout, unavailable, resource-exhausted, invalid-request, model-not-ready, cancelled, or internal. They must separately mark retryability. The executor, not the provider adapter and not an LLM, applies the persisted retry budget.

Invalid requests, cancellations, and deterministic validation failures should normally be non-retryable. Temporary availability and capacity failures may be retryable when the provider contract guarantees safe resubmission under the job idempotency key.

## Routing

`ModelRouter` receives task, quality profile, required capabilities, generation strategy, and logical resource class. It returns an auditable `ProviderSelection`. Provider/model names remain adapter configuration and do not alter roles, shots, workflows, or artifact contracts.

Routing can later include cost, locality, policy, or measured reliability, but it must remain deterministic for the same capability snapshot or record the reason and inputs used for the choice.

## Deployment boundary

GPU placement, Spark/DGX access, ComfyUI workflows, vLLM servers, ModelScope downloads, credentials, batching, quantization, and offload are provider-runtime concerns. They may be introduced behind these interfaces in a later phase. Secrets must remain in runtime configuration and never enter domain JSON, artifact payloads, events, or checkpoints.

## Definition of ready for a real adapter

- capability and health checks are tested;
- request translation is isolated;
- status and cancellation are idempotent;
- all errors are normalized and retryability is justified;
- generated outputs register immutable artifact versions;
- provenance is sufficient to explain an output;
- no concrete provider field leaked into Cinematic IR;
- local mock workflow remains green without the adapter installed.
