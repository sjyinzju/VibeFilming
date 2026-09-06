"""Model-agnostic inference policy shared by role orchestration and transport."""

from pydantic import Field
from movie_agent.domain import ContractModel


class RoleInferencePolicy(ContractModel):
    """Optional role overrides; read/output/thinking inherit serving defaults."""

    connect_timeout: float = Field(default=10, gt=0)
    read_timeout: float | None = Field(default=None, gt=0)
    write_timeout: float = Field(default=30, gt=0)
    pool_timeout: float = Field(default=10, gt=0)
    max_output_tokens: int | None = Field(default=None, ge=128, le=32768)
    thinking: bool | None = None
    structured_output: bool = True
    stream: bool = False
    inactivity_timeout: float = Field(default=60, gt=0)
    total_timeout: float | None = Field(default=None, gt=0)

    def resolve(self, defaults: "RoleInferencePolicy", *, max_tokens: int) -> "RoleInferencePolicy":
        return self.model_copy(update={
            "read_timeout": self.read_timeout if self.read_timeout is not None else defaults.read_timeout,
            "max_output_tokens": min(self.max_output_tokens or max_tokens, max_tokens,
                                     defaults.max_output_tokens or max_tokens),
            "thinking": self.thinking if self.thinking is not None else defaults.thinking,
            "total_timeout": self.total_timeout if self.total_timeout is not None else defaults.total_timeout,
        })
