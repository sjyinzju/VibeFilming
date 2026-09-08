"""Production composition root for the verified Spark deployment."""

from movie_agent.domain import ProviderKind, ResourceClass
from movie_agent.media import MediaModality, ProviderCapabilities, ResourceProfile
from .contracts import ModelServiceDescriptor
from .manager import ModelManager
from .resources import GiB, ModelRuntimeProfile, ResourceRuntimeSettings
from .spark import SparkDockerModelService, SparkDockerServiceController, SparkResourceTelemetry
from .coordinator import RuntimeCoordinator
from .ownership import RuntimeOwnership


def build_spark_runtime(llm_config, media_settings, *, settings=None):
    settings = settings or ResourceRuntimeSettings.from_env()
    manager = ModelManager()
    controller = SparkDockerServiceController(settings)
    specs = [
        ("qwen", "reasoning", llm_config.model, MediaModality.TEXT, ProviderKind.LLM,
         llm_config.base_url.removesuffix("/v1"), "/v1/models", "/metrics", 48, 64, 180,
         "Conservative 64 GiB budget; isolated Qwen peak not yet measured"),
        ("flux", "flux_direct", "FLUX.1-dev", MediaModality.IMAGE, ProviderKind.IMAGE,
         media_settings.flux_endpoint, "/health", "/health", 32, 44, 230,
         "Accepted FLUX CUDA reservation ~36.1 GiB; 44 GiB includes host/activation allowance"),
        ("comfyui", "comfyui-video", media_settings.comfyui_workflow_profile, MediaModality.VIDEO, ProviderKind.VIDEO,
         media_settings.comfyui_endpoint, "/system_stats", "/queue", 48, 104, 180,
         "P4A isolated 22-frame H3 sampled pressure 103.73 GiB; rounded up to 104 GiB, plus separate safety headroom"),
    ]
    for sid, pid, model, modality, kind, endpoint, health, idle, resident, peak, cost, basis in specs:
        descriptor = ModelServiceDescriptor(service_id=sid, modality=modality, endpoint=endpoint,
            capabilities=ProviderCapabilities(provider_id=pid, kind=kind, modalities=[modality]),
            resource_profile=ResourceProfile(resource_class=ResourceClass.HEAVY,
                expected_memory_gb=peak, supports_concurrency=False,
                requires_exclusive_runtime=sid == "comfyui"),
            runtime_profile=ModelRuntimeProfile(service_id=sid, provider_id=pid,
                model_profile_id=model, estimated_resident_bytes=resident * GiB,
                estimated_peak_bytes=peak * GiB, estimate_basis=basis,
                startup_cost_seconds=cost, requires_exclusive_runtime=sid == "comfyui"))
        headers = {"Authorization": f"Bearer {llm_config.api_key.get_secret_value()}"} if sid == "qwen" else {}
        manager.register(SparkDockerModelService(descriptor, controller,
            health_path=health, idle_path=idle, kind=sid, headers=headers))
    # All projects/workspaces on this serving host share one physical Spark and journal.
    from pathlib import Path
    shared = Path(settings.state_path).resolve()
    owner = RuntimeOwnership(shared.with_suffix(".owner")) if settings.enabled else None
    try:
        runtime = RuntimeCoordinator(manager, SparkResourceTelemetry(controller), settings=settings, state_path=shared)
        runtime.ownership = owner
        from movie_agent.execution.durable_events import DurableLocalEventBus
        runtime.bind("resource-runtime", DurableLocalEventBus(shared.parent / "events"), "trace_resource_runtime")
        return runtime
    except BaseException:
        if owner:
            owner.close()
        raise
