"""Named application runtime profiles; model paths/endpoints remain in provider settings."""
import os
from movie_agent.providers.registry import MediaProviderSettings


def production_media_settings(root,*,profile=None,base=None):
    profile=profile or os.environ.get('MOVIE_AGENT_PRODUCTION_PROFILE','installed')
    base=base or MediaProviderSettings.from_env()
    if profile=='configured':return base
    if profile!='installed':raise ValueError('Unknown production runtime profile')
    # Existing supported adapters. No downloads, launches or media requests happen here.
    return base.model_copy(update={'image_provider':'flux_direct','kontext_enabled':True,
        'video_provider':'comfyui','vision_provider':'qwen3_vl','audio_provider':'real','post_provider':'ffmpeg',
        'vision_reasoning_parser':'qwen3','vision_temperature':.6,
        'audio_task_state_root':str(root/'tasks'),'post_temp_root':str(root/'temp')})
