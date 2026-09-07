"""Optional user-authored inspiration at the application boundary, never cinematic IR."""

from typing import Literal
from pydantic import Field, field_validator
from movie_agent.domain import ContractModel, ProjectBrief, new_id
from movie_agent.media.contracts import MediaReference
from movie_agent.orchestration.runtime.context import ContextBuilder, content_hash


class SceneSeed(ContractModel):
    hint_id: str = Field(default_factory=lambda: new_id("hint"))
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)


class KeyMoment(ContractModel):
    hint_id: str = Field(default_factory=lambda: new_id("hint"))
    description: str = Field(min_length=1)
    scene_hint: str | None = None


class KeyVisualHint(ContractModel):
    hint_id: str = Field(default_factory=lambda: new_id("hint"))
    description: str = Field(min_length=1)
    reference: str | None = None


class StyleReference(ContractModel):
    hint_id: str = Field(default_factory=lambda: new_id("hint"))
    title: str = Field(min_length=1)
    kind: Literal["film", "series", "director", "photography", "visual"] = "film"
    dimensions: list[Literal["visual", "lighting", "cinematography", "color", "editing_rhythm", "narrative_tone"]] = Field(default_factory=list)


class CreativeHints(ContractModel):
    author: Literal["user"] = "user"
    scene_seeds: list[SceneSeed] = Field(default_factory=list)
    key_moments: list[KeyMoment] = Field(default_factory=list)
    key_visuals: list[KeyVisualHint] = Field(default_factory=list)
    style_references: list[StyleReference] = Field(default_factory=list)
    media_references: list[MediaReference] = Field(default_factory=list)


class CreateProjectInput(ProjectBrief):
    """Accepts every existing POST body; supplies convenience defaults only at ingress."""
    title: str = Field(default="Untitled film", min_length=1)
    logline: str = Field(default="A story waiting to unfold.", min_length=1)
    target_duration: float = Field(default=30, gt=0)
    creative_hints: CreativeHints = Field(default_factory=CreativeHints)
    draft_id: str | None = Field(default=None, pattern=r"^draft_[A-Za-z0-9-]{8,80}$")

    @field_validator("story_description", "title", "logline")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Must not be blank")
        return value.strip()

    def canonical_brief(self) -> ProjectBrief:
        return ProjectBrief.model_validate(self.model_dump(exclude={"creative_hints", "draft_id"}))


class CreativeInputContextBuilder(ContextBuilder):
    """Inject persisted, labelled user input without changing canonical state ownership."""
    def __init__(self, hints: CreativeHints):
        self.hints = hints

    def build(self, definition, project, *, scene=None):
        context = super().build(definition, project, scene=scene)
        if not any((self.hints.scene_seeds, self.hints.key_moments,
                    self.hints.key_visuals, self.hints.style_references)):
            return context  # Exactly the frozen context/hash for legacy projects.
        data = self.hints.model_dump(mode="json")
        # Binary references are consumed only by explicit media contracts, never reasoning prompts.
        data.pop("media_references", None)
        for collection in ("scene_seeds", "key_moments", "key_visuals", "style_references"):
            for item in data.get(collection, []):
                item.pop("hint_id", None)
        # Scene seeds describe proposed scenes, not authoritative Scene IDs/state.
        if scene is not None:
            data.pop("scene_seeds")
        context.payload["user_authored_creative_hints"] = data
        context.source_ids.append(project.project_id + ":creative_hints")
        context.context_hash = content_hash(context.payload)
        context.context_version += "+creative-input/1"
        return context
