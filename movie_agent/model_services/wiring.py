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
        ("vlm", "qwen3_vl", "Qwen3-VL-30B-A3B-Thinking", MediaModality.VISION, ProviderKind.VISION,
         media_settings.vision_endpoint.removesuffix("/v1"), "/v1/models", "/metrics",
         media_settings.vision_resident_gib, media_settings.vision_peak_gib, 180,
         media_settings.vision_estimate_basis),
        ('tts', 'qwen3_tts', media_settings.tts_model, MediaModality.AUDIO, ProviderKind.AUDIO,
         media_settings.tts_endpoint, '/health', '/health', media_settings.tts_resident_gib,
         media_settings.tts_peak_gib, media_settings.tts_startup_seconds, media_settings.tts_estimate_basis),
        ('music', 'ace_step', media_settings.music_model, MediaModality.AUDIO, ProviderKind.AUDIO,
         media_settings.music_endpoint, '/v1/model_inventory', '/v1/stats', media_settings.music_resident_gib,
         media_settings.music_peak_gib, media_settings.music_startup_seconds, media_settings.music_estimate_basis),
    ]
    if media_settings.kontext_enabled:
        specs.append(('kontext', 'flux_kontext', 'FLUX.1-Kontext-dev-NVFP4', MediaModality.IMAGE, ProviderKind.IMAGE,
            media_settings.kontext_endpoint, '/health', '/health', media_settings.kontext_resident_gib,
            media_settings.kontext_peak_gib, media_settings.kontext_startup_seconds, media_settings.kontext_estimate_basis))
    specs = [spec for spec in specs if spec[0] not in {'tts','music'}
             or media_settings.audio_provider in {'real',spec[1]}]
    for sid, pid, model, modality, kind, endpoint, health, idle, resident, peak, cost, basis in specs:
        descriptor = ModelServiceDescriptor(service_id=sid, modality=modality, endpoint=endpoint,
            capabilities=ProviderCapabilities(provider_id=pid, kind=kind, modalities=[modality],
                requires_resource_lease=True),
            metadata=({"served_model": media_settings.vision_model, "container": "movie-agent-vlm"} if sid == "vlm"
                else {"minimum_cold_start_free_bytes": 48 * GiB,
                      "startup_free_basis": "FLUX allocator requires 40 GiB plus its 8 GiB reserve; P5 co-resident Qwen startup allowed only 6.81 GiB and OOMed"}
                if sid == 'flux' else {}),
            resource_profile=ResourceProfile(resource_class=ResourceClass.HEAVY,
                expected_memory_gb=peak or None, supports_concurrency=False,
                requires_exclusive_runtime=sid in {"comfyui", "vlm"}),
            runtime_profile=ModelRuntimeProfile(service_id=sid, provider_id=pid,
                model_profile_id=model, estimated_resident_bytes=int(resident * GiB),
                estimated_peak_bytes=int(peak * GiB), estimate_basis=basis,
                startup_cost_seconds=cost, requires_exclusive_runtime=sid in {"comfyui", "vlm"}))
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
