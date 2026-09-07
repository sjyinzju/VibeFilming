"""Provider abstractions, deterministic routing, and local mocks."""

from movie_agent.providers.base import *  # noqa: F401,F403
from movie_agent.providers.mock import *  # noqa: F401,F403
from movie_agent.providers.router import *  # noqa: F401,F403
from movie_agent.providers.media import *  # noqa: F401,F403
from movie_agent.providers.registry import *  # noqa: F401,F403


def __getattr__(name: str):
    """Load the ComfyUI adapter lazily to keep its client boundary cycle-free."""

    if name == "ComfyUIVideoProvider":
        from movie_agent.providers.comfyui_video import ComfyUIVideoProvider
        return ComfyUIVideoProvider
    raise AttributeError(name)
