"""Provider abstraction, artifact versioning, and event-stream tests."""

import asyncio

from movie_agent.artifacts import LocalArtifactStore
from movie_agent.domain import (
    ArtifactType,
    EventEnvelope,
    EventType,
    GenerationRequest,
    GenerationStrategy,
    GenerationStrategyType,
    PromptPackage,
    ProviderErrorType,
    ProviderKind,
    ProviderRequest,
    QualityProfile,
    ResourceClass,
    RoutingRequest,
)
from movie_agent.execution import LocalEventBus
from movie_agent.providers import MockProvider, ModelRouter, ProviderFailure, normalize_provider_error


def generation_request(job_id: str = "job_1") -> GenerationRequest:
    strategy = GenerationStrategy(
        strategy_type=GenerationStrategyType.FIRST_LAST_FRAME_TO_VIDEO,
        reason="test",
        required_capabilities=["first_frame", "last_frame"],
    )
    return GenerationRequest(
        job_id=job_id,
        task="video",
        strategy=strategy,
        prompt_package=PromptPackage(
            compiler_id="generic",
            compiler_version="1.0.0",
            positive_prompt="test shot",
        ),
        requested_output_type="video",
    )


def test_provider_error_normalization() -> None:
    timeout = normalize_provider_error("req_1", TimeoutError("slow"))
    invalid = normalize_provider_error("req_2", ValueError("bad payload"))
    exhausted = normalize_provider_error(
        "req_3",
        ProviderFailure(
            "capacity",
            error_type=ProviderErrorType.RESOURCE_EXHAUSTED,
            retryable=True,
        ),
    )

    assert timeout.error_type == ProviderErrorType.TIMEOUT and timeout.retryable
    assert invalid.error_type == ProviderErrorType.INVALID_REQUEST and not invalid.retryable
    assert exhausted.error_type == ProviderErrorType.RESOURCE_EXHAUSTED and exhausted.retryable


def test_mock_provider_and_model_router() -> None:
    async def scenario() -> None:
        provider = MockProvider("video-a", ProviderKind.VIDEO)
        router = ModelRouter([provider])
        selection = await router.select(
            RoutingRequest(
                task="video",
                quality_profile=QualityProfile.STANDARD,
                required_capabilities=["first_frame", "last_frame"],
                strategy_type=GenerationStrategyType.FIRST_LAST_FRAME_TO_VIDEO,
                resource_class=ResourceClass.MEDIUM,
            )
        )
        request = ProviderRequest(
            provider_id=selection.provider_id,
            generation_request=generation_request(),
        )
        result = await router.get(selection.provider_id).submit(request)
        assert result.success
        assert await provider.status(request.provider_request_id) == result
        assert await provider.cancel("future_request")

    asyncio.run(scenario())


def test_artifact_versions_never_overwrite_and_reload(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    first = store.create_placeholder(
        ArtifactType.VIDEO,
        "first version",
        artifact_id="shot07",
        extension="mp4.placeholder",
    )
    second = store.create_placeholder(
        ArtifactType.VIDEO,
        "second version",
        artifact_id="shot07",
        parent_artifact_ids=[first.artifact_id],
        extension="mp4.placeholder",
    )
    selected = store.select("shot07", 2)

    assert first.version == 1 and second.version == 2
    assert first.uri != second.uri
    assert selected.selected
    assert store.get("shot07").version == 2
    assert len(store.list_versions("shot07")) == 2

    restored = LocalArtifactStore(tmp_path / "artifacts")
    assert restored.get("shot07", 1).uri == first.uri
    assert restored.get("shot07").version == 2


def test_event_bus_emits_in_order_and_filters() -> None:
    bus = LocalEventBus()
    observed: list[str] = []
    bus.subscribe(lambda event: observed.append(event.event_id), EventType.JOB_STARTED)
    completed = EventEnvelope(
        event_id="event_2",
        event_type=EventType.JOB_COMPLETED,
        project_id="project_1",
        trace_id="trace_1",
    )
    started = EventEnvelope(
        event_id="event_1",
        event_type=EventType.JOB_STARTED,
        project_id="project_1",
        trace_id="trace_1",
    )

    bus.emit(started)
    bus.emit(completed)

    assert [event.event_id for event in bus.events()] == ["event_1", "event_2"]
    assert [event.event_id for event in bus.events(EventType.JOB_COMPLETED)] == ["event_2"]
    assert observed == ["event_1"]
