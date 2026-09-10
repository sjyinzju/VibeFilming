"""Request-only cinematographer types and deterministic mapping into Cinematic IR."""

from pydantic import Field, PositiveFloat, model_validator

from movie_agent.domain import (
    CameraSpec, CharacterState, ContinuityChain, ContinuityState, ContractModel,
    FrameAnchor, FrameAnchors, LightingSpec, LocationState, PerformanceSpec,
    Project, PropState, Scene, Shot, ShotNarrative, Vector3,
)
from movie_agent.domain.enums import Orientation, PropCondition
from .contracts import OutputErrorCode as Code, OutputIssue, ValidationReport

MAPPER_VERSION = "cinematographer-draft-mapper/4"


class CanonicalChainContext(ContractModel):
    """Core-owned input only; deliberately absent from ShotPlanDraft output."""

    scene_id: str
    initial_state: ContinuityState
    expected_final_state: ContinuityState
    canonical_character_ids: list[str]
    canonical_location_ids: list[str]
    canonical_prop_ids: list[str]


class CharacterLocalStateDraft(ContractModel):
    """Scene-local character target; null fields inherit the canonical current state."""

    character_id: str
    position: Vector3 | None = None
    orientation: Orientation | None = None
    pose: str | None = None
    wardrobe: str | None = None
    hair: str | None = None
    emotion: str | None = None
    held_prop_ids: list[str] | None = None
    visible: bool | None = None
    damage_state: str | None = None


class PropLocalStateDraft(ContractModel):
    """Scene-local prop target; the entity itself must belong to Scene.prop_ids."""

    prop_id: str
    condition: PropCondition | None = None
    holder_character_id: str | None = None
    clear_holder_character_id: bool = False
    position: Vector3 | None = None
    visible: bool | None = None
    notes: str | None = None


class LocationLocalStateDraft(ContractModel):
    """Scene-local location target; null fields inherit the canonical current state."""

    location_id: str
    scene_state: list[str] | None = None
    lighting_state: str | None = None
    weather: str | None = None
    damage_state: str | None = None
    time_of_day: str | None = None


class ShotLocalStateDraft(ContractModel):
    """Only local changes/targets. Omitted entities and null fields inherit unchanged."""

    character_updates: list[CharacterLocalStateDraft] = Field(default_factory=list)
    location_updates: list[LocationLocalStateDraft] = Field(default_factory=list)
    prop_updates: list[PropLocalStateDraft] = Field(default_factory=list)
    scene_state: list[str] | None = None
    lighting_state: str | None = None
    weather: str | None = None
    damage_state: str | None = None


class FramePlanningDraft(ContractModel):
    """Creative frame intent only; artifact and topology references remain Core-owned."""

    first_frame_description: str | None = None
    last_frame_description: str | None = None


class ShotDraft(ContractModel):
    """LLM-owned cinematic decisions without canonical IDs, topology, or snapshots."""

    narrative: ShotNarrative
    duration_seconds: PositiveFloat
    camera: CameraSpec
    performances: list[PerformanceSpec] = Field(default_factory=list)
    lighting: LightingSpec
    visual_requirements: list[str] = Field(default_factory=list)
    frame_planning: FramePlanningDraft = Field(default_factory=FramePlanningDraft)
    local_state_delta: ShotLocalStateDraft = Field(default_factory=ShotLocalStateDraft)


class ShotPlanDraft(ContractModel):
    """LLM-owned shot choices; canonical facts/state/topology are Core-owned."""

    preserved_constraints: list[str]
    shots: list[ShotDraft] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_immutable_facts(cls, value):
        """Accept persisted pre-v3 drafts without treating their fact copy as authoritative."""
        if isinstance(value, dict) and "immutable_facts" in value:
            value = {key: item for key, item in value.items() if key != "immutable_facts"}
        return value


class CinematographerDraftMapper:
    """Validate local ownership, then deterministically construct existing core IR."""

    version = MAPPER_VERSION

    @staticmethod
    def validate_local(draft: ShotPlanDraft, project: Project, scene: Scene) -> ValidationReport:
        report = ValidationReport()
        catalogs = {
            "character": {item.character_id for item in project.characters},
            "location": {item.location_id for item in project.locations},
            "prop": {item.prop_id for item in project.props},
        }
        allowed = {"character": set(scene.character_ids), "location": {scene.location_id},
                   "prop": set(scene.prop_ids)}

        def add(code, path, message):
            report.issues.append(OutputIssue(code=code, path=path, message=message))

        for shot_index, shot in enumerate(draft.shots):
            delta = shot.local_state_delta
            collections = (("character", delta.character_updates, "character_id"),
                           ("location", delta.location_updates, "location_id"),
                           ("prop", delta.prop_updates, "prop_id"))
            for kind, updates, id_field in collections:
                ids = [getattr(item, id_field) for item in updates]
                if len(ids) != len(set(ids)):
                    add(Code.SEMANTIC_CONFLICT, f"shots.{shot_index}.local_state_delta.{kind}_updates",
                        f"Conflicting duplicate {kind} updates are not allowed")
                for entity_id in ids:
                    path = f"shots.{shot_index}.local_state_delta.{kind}_updates.{entity_id}"
                    if entity_id not in catalogs[kind]:
                        add(Code.UNKNOWN_ENTITY_ID, path, f"Unknown {kind} ID: {entity_id}")
                    elif entity_id not in allowed[kind]:
                        add(Code.BROKEN_REFERENCE, path,
                            f"Ambient/cross-scene {kind} ID is not local to {scene.scene_id}: {entity_id}")
                    elif entity_id not in getattr(scene.initial_state, kind + "_states"):
                        add(Code.SEMANTIC_CONFLICT, path,
                            f"Local {kind} has no canonical initial state to update: {entity_id}")
            for update in delta.character_updates:
                canonical=scene.initial_state.character_states.get(update.character_id)
                ambient=set(canonical.held_prop_ids if canonical else [])-allowed['prop']
                supplied=set(update.held_prop_ids or [])-allowed['prop']
                if update.held_prop_ids is not None and supplied!=ambient:
                    add(Code.BROKEN_REFERENCE,f'shots.{shot_index}.held_prop_ids',
                        'Local character update cannot add/remove canonical ambient prop relationships')
                for prop_id in update.held_prop_ids or []:
                    if prop_id not in catalogs["prop"]:
                        add(Code.UNKNOWN_ENTITY_ID, f"shots.{shot_index}.held_prop_ids",
                            f"Unknown prop ID: {prop_id}")
                    elif prop_id not in allowed["prop"] and prop_id not in ambient:
                        add(Code.BROKEN_REFERENCE, f"shots.{shot_index}.held_prop_ids",
                            f"Held prop is not local to {scene.scene_id}: {prop_id}")
            for update in delta.prop_updates:
                if update.holder_character_id is not None and update.clear_holder_character_id:
                    add(Code.SEMANTIC_CONFLICT, f"shots.{shot_index}.holder_character_id",
                        "Cannot set and clear holder_character_id in the same local update")
                if update.holder_character_id is not None:
                    if update.holder_character_id not in catalogs["character"]:
                        add(Code.UNKNOWN_ENTITY_ID, f"shots.{shot_index}.holder_character_id",
                            f"Unknown character ID: {update.holder_character_id}")
                    elif update.holder_character_id not in allowed["character"]:
                        add(Code.BROKEN_REFERENCE, f"shots.{shot_index}.holder_character_id",
                            f"Holder is not local to {scene.scene_id}: {update.holder_character_id}")
        return report

    @staticmethod
    def _changes(update, identity: str) -> dict:
        # Keep nested ContractModel instances typed. ``model_dump()`` turns a
        # Vector3 into a plain dict, while Pydantic's ``model_copy(update=...)``
        # deliberately does not revalidate updates. That representation leak
        # made equal canonical positions compare as dict != Vector3.
        changes = {name: getattr(update, name) for name in type(update).model_fields
                   if name not in {"schema_version", identity, "clear_holder_character_id"}
                   and getattr(update, name) is not None}
        if getattr(update, "clear_holder_character_id", False):
            changes["holder_character_id"] = None
        return changes

    def apply_local_delta(self, current: ContinuityState, delta: ShotLocalStateDraft) -> ContinuityState:
        result = current.model_copy(deep=True)
        for update in delta.character_updates:
            previous = result.character_states[update.character_id]
            result.character_states[update.character_id] = previous.model_copy(
                update=self._changes(update, "character_id"), deep=True)
        for update in delta.location_updates:
            previous = result.location_states[update.location_id]
            result.location_states[update.location_id] = previous.model_copy(
                update=self._changes(update, "location_id"), deep=True)
        for update in delta.prop_updates:
            previous = result.prop_states[update.prop_id]
            result.prop_states[update.prop_id] = previous.model_copy(
                update=self._changes(update, "prop_id"), deep=True)
        for field in ("scene_state", "lighting_state", "weather", "damage_state"):
            value = getattr(delta, field)
            if value is not None:
                setattr(result, field, value)
        return result

    def map(self, draft: ShotPlanDraft, project: Project, scene: Scene) -> tuple[object, ValidationReport]:
        from movie_agent.domain import ShotPlan

        report = self.validate_local(draft, project, scene)
        if not report.valid:
            return None, report
        chain_id = f"{scene.scene_id}-CHAIN"
        current = scene.initial_state.model_copy(deep=True)
        shots = []
        shot_ids = [f"{scene.scene_id}-SHOT-{index + 1:02d}" for index in range(len(draft.shots))]
        for index, item in enumerate(draft.shots):
            shot_id = shot_ids[index]
            after = self.apply_local_delta(current, item.local_state_delta)
            after.previous_shot_id = shot_id
            anchors = FrameAnchors(
                first_frame=FrameAnchor(description=item.frame_planning.first_frame_description),
                last_frame=FrameAnchor(description=item.frame_planning.last_frame_description))
            shots.append(Shot(shot_id=shot_id, scene_id=scene.scene_id,
                narrative=item.narrative, duration_seconds=item.duration_seconds, camera=item.camera,
                performances=item.performances, lighting=item.lighting,
                visual_requirements=item.visual_requirements, frame_anchors=anchors,
                continuity_chain_id=chain_id,
                previous_shot_id=shot_ids[index - 1] if index else None,
                next_shot_id=shot_ids[index + 1] if index + 1 < len(shot_ids) else None,
                state_before=current.model_copy(deep=True), expected_state_after=after.model_copy(deep=True),
                generation_strategy=None, retry_budget=project.brief.max_retry,
                quality_profile=project.brief.quality_level))
            current = after
        plan = ShotPlan(scene_id=scene.scene_id, shots=shots,
            continuity_chains=[ContinuityChain(chain_id=chain_id, label=scene.title,
                shot_ids=shot_ids, initial_state=scene.initial_state.model_copy(deep=True))],
            preserved_constraints=draft.preserved_constraints,
            immutable_facts=list(project.story_bible.immutable_facts) if project.story_bible else [])
        return plan, report
