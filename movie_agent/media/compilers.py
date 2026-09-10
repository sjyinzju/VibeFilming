"""Generic provider-neutral prompt compilers for every media family."""

from __future__ import annotations

from typing import Protocol

from movie_agent.domain import PromptPackage, PromptSection, Shot
from movie_agent.media.contracts import AudioPurpose, ImagePurpose, MediaGenerationStrategy, MediaReference


def _reference_section(references: list[MediaReference]) -> PromptSection:
    values = [f"{item.reference_type.value}:{item.artifact_uri} sha256={item.sha256 or 'unresolved'}" for item in references]
    return PromptSection(name="references", content="; ".join(values) or "none")


def continuity_sections(shot):
    def describe(state):
        facts = []
        for cid, value in sorted(state.character_states.items()):
            facts.append(f'{cid}: visible={value.visible}; wardrobe={value.wardrobe}; pose={value.pose}; '
                         f'emotion={value.emotion}; physical state={value.damage_state}; holds={", ".join(value.held_prop_ids)}')
        for lid, value in sorted(state.location_states.items()):
            facts.append(f'{lid}: {"; ".join(value.scene_state)}; lighting={value.lighting_state}')
        for pid, value in sorted(state.prop_states.items()):
            facts.append(f'{pid}: {value.condition.value}; holder={value.holder_character_id}; {value.notes or ""}')
        return '; '.join([*facts,*state.scene_state,*([state.lighting_state] if state.lighting_state else [])])
    return [PromptSection(name=name,content=value) for name,value in (
        ('current_state',describe(shot.state_before)),
        ('intended_change',describe(shot.expected_state_after)),
        ('must_not_change','; '.join(shot.camera.composition.preserve))) if value]


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
        *,
        has_authoritative_dialogue: bool = False,
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
        sections.extend(continuity_sections(shot))
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
    compiler_version = "1.1.0"

    def compile(
        self,
        shot: Shot,
        strategy: MediaGenerationStrategy,
        references: list[MediaReference],
        repair_context=None,
        has_authoritative_dialogue: bool = False,
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
        if repair_context:
            sections.extend(repair_prompt_sections(repair_context))
        sections.extend(continuity_sections(shot))
        if has_authoritative_dialogue:
            sections.append(PromptSection(name='production_sound', content=(
                'No intelligible spoken dialogue. Generate environmental ambience, '
                'foley and non-verbal vocal sounds only.')))
        return PromptPackage(
            compiler_id=self.compiler_id,
            compiler_version=self.compiler_version,
            positive_prompt="\n".join(f"[{item.name}] {item.content}" for item in sections),
            negative_prompt="No temporal flicker, identity drift, undeclared entities, or incomplete action.",
            sections=sections,
            source_shot_id=shot.shot_id,
        )


def repair_prompt_sections(context):
    sections = [PromptSection(name=name, content="\n".join(getattr(context, name)))
                for name in ("preserve", "fix", "avoid", "evidence") if getattr(context, name)]
    if context.raw_feedback:
        sections.append(PromptSection(name="human_feedback", content=context.raw_feedback))
    return sections


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
