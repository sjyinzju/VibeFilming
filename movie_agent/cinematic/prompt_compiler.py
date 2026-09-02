"""Prompt compiler boundary from Cinematic IR to provider text packages."""

from __future__ import annotations

from typing import Protocol

from movie_agent.domain import PromptPackage, PromptSection, Shot


class PromptCompiler(Protocol):
    """Compile stable Cinematic IR without mutating the source shot."""

    def compile(self, shot: Shot) -> PromptPackage: ...


class GenericPromptCompiler:
    """Generic compiler used only by mocks and contract tests."""

    compiler_id = "generic"
    compiler_version = "1.0.0"

    def compile(self, shot: Shot) -> PromptPackage:
        performances = "; ".join(
            f"{performance.character_id}: {performance.action} "
            f"({performance.emotion_start or 'unspecified'} to "
            f"{performance.emotion_end or 'unspecified'})"
            for performance in shot.performances
        ) or "No character performance specified"
        camera = (
            f"{shot.camera.shot_size.value}, {shot.camera.angle.value}, "
            f"{shot.camera.lens_mm or 'unspecified'}mm, "
            f"{shot.camera.motion.motion_type.value}"
        )
        sections = [
            PromptSection(name="narrative", content=f"{shot.narrative.purpose}: {shot.narrative.beat}"),
            PromptSection(name="camera", content=camera),
            PromptSection(name="performance", content=performances),
            PromptSection(name="lighting", content=shot.lighting.setup),
            PromptSection(name="continuity", content="; ".join(shot.visual_requirements)),
        ]
        positive = "\n".join(f"[{section.name}] {section.content}" for section in sections)
        return PromptPackage(
            compiler_id=self.compiler_id,
            compiler_version=self.compiler_version,
            positive_prompt=positive,
            negative_prompt="Do not violate declared continuity or add undeclared characters or props.",
            sections=sections,
            source_shot_id=shot.shot_id,
        )

