"""Runtime configuration; endpoint and secrets never enter cinematic contracts."""

from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Mapping
from urllib.parse import urlparse

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class LLMConfig(BaseModel):
    """Serving configuration loaded explicitly, with environment taking precedence."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    base_url: str
    model: str = Field(min_length=1)
    api_key: SecretStr = SecretStr("")
    timeout: float = Field(default=180, gt=0)
    max_tokens: int = Field(default=16000, ge=128, le=32768)
    enable_thinking: bool = False

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("base_url must be an HTTP(S) URL without embedded credentials")
        return value.rstrip("/")

    @classmethod
    def from_env(cls, path: str | Path = ".env", *, environ: Mapping[str, str] | None = None) -> "LLMConfig":
        values = {**dotenv_values(path), **(os.environ if environ is None else environ)}
        names = ("base_url", "model", "api_key", "timeout", "max_tokens", "enable_thinking")
        return cls.model_validate({
            name: values[f"MOVIE_AGENT_LLM_{name.upper()}"]
            for name in names if values.get(f"MOVIE_AGENT_LLM_{name.upper()}") not in (None, "")
        })
