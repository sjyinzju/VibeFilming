"""Generic provider-neutral prompt compilers for every media family."""

from __future__ import annotations

from typing import Protocol

from movie_agent.domain import PromptPackage, PromptSection, Shot
from movie_agent.media.contracts import AudioPurpose, ImagePurpose, MediaGenerationStrategy, MediaReference


def _reference_section(references: list[MediaReference]) -> PromptSection:
    values = [f"{item.reference_type.value}:{item.artifact_id}" for item in references]
    return PromptSection(name="references", content="; ".join(values) or "none")


class ImagePromptCompiler(Protocol):
    def compile(
        self,
        shot: Shot,
        purpose: ImagePurpose,
        references: list[MediaReference],
    ) -> PromptPackage: ...


class VideoPromptCompiler(Protocol):
    def compile(
        self,
        shot: Shot,
        strategy: MediaGenerationStrategy,
        references: list[MediaReference],
    ) -> PromptPackage: ...


class AudioPromptCompiler(Protocol):
    def compile(
        self,
        purpose: AudioPurpose,
        description: str,
        references: list[MediaReference],
        *,
        shot: Shot | None = None,
    ) -> PromptPackage: ...


class GenericImagePromptCompiler:
    compiler_id = "generic-image"
    compiler_version = "1.0.0"

    def compile(
        self,
        shot: Shot,
        purpose: ImagePurpose,
        references: list[MediaReference],
    ) -> PromptPackage:
        sections = [
            PromptSection(name="purpose", content=purpose.value),
            PromptSection(name="narrative", content=shot.narrative.action_summary or shot.narrative.beat),
            PromptSection(
                name="composition",
                content=f"{shot.camera.shot_size.value}; {shot.camera.composition.framing}",
            ),
            PromptSection(name="lighting", content=shot.lighting.setup),
            PromptSection(name="continuity", content="; ".join(shot.visual_requirements)),
            _reference_section(references),
        ]
        return PromptPackage(
            compiler_id=self.compiler_id,
            compiler_version=self.compiler_version,
            positive_prompt="\n".join(f"[{item.name}] {item.content}" for item in sections),
            negative_prompt="No undeclared characters, props, identity changes, text, or continuity breaks.",
            sections=sections,
            source_shot_id=shot.shot_id,
        )


class GenericVideoPromptCompiler:
    compiler_id = "generic-video"
    compiler_version = "1.0.0"

    def compile(
        self,
        shot: Shot,
        strategy: MediaGenerationStrategy,
        references: list[MediaReference],
    ) -> PromptPackage:
        performances = "; ".join(
            f"{item.character_id}: {item.action}" for item in shot.performances
        ) or "no character performance"
        motion = shot.camera.motion
        sections = [
            PromptSection(name="narrative", content=f"{shot.narrative.purpose}: {shot.narrative.beat}"),
            PromptSection(name="action", content=shot.narrative.action_summary),
            PromptSection(name="performance", content=performances),
            PromptSection(
                name="camera",
                content=(
                    f"{shot.camera.shot_size.value}; {shot.camera.angle.value}; "
                    f"{shot.camera.lens_mm or 'unspecified'}mm; {motion.motion_type.value}; "
                    f"direction={motion.direction or 'unspecified'}; speed={motion.speed or 'unspecified'}"
                ),
            ),
            PromptSection(name="lighting", content=shot.lighting.setup),
            PromptSection(name="strategy", content=strategy.strategy_type.value),
            PromptSection(name="continuity", content="; ".join(shot.visual_requirements)),
            _reference_section(references),
        ]
        return PromptPackage(
            compiler_id=self.compiler_id,
            compiler_version=self.compiler_version,
            positive_prompt="\n".join(f"[{item.name}] {item.content}" for item in sections),
            negative_prompt="No temporal flicker, identity drift, undeclared entities, or incomplete action.",
            sections=sections,
            source_shot_id=shot.shot_id,
        )


class GenericAudioPromptCompiler:
    compiler_id = "generic-audio"
    compiler_version = "1.0.0"

    def compile(
        self,
        purpose: AudioPurpose,
        description: str,
        references: list[MediaReference],
        *,
        shot: Shot | None = None,
    ) -> PromptPackage:
        sections = [
            PromptSection(name="purpose", content=purpose.value),
            PromptSection(name="description", content=description),
            PromptSection(
                name="shot_context",
                content=shot.narrative.action_summary if shot else "project-level audio",
            ),
            _reference_section(references),
        ]
        return PromptPackage(
            compiler_id=self.compiler_id,
            compiler_version=self.compiler_version,
            positive_prompt="\n".join(f"[{item.name}] {item.content}" for item in sections),
            negative_prompt="No clipping, unintended speech, abrupt cuts, or mismatched ambience.",
            sections=sections,
            source_shot_id=shot.shot_id if shot else None,
        )

