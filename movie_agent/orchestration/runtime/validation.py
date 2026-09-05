"""Deterministic role-output checks; free-form semantic truth is not claimed proven."""

import re
from pydantic import ValidationError
from movie_agent.domain import (
    Project, CreativeDirection, StoryBible, VisualBible, Screenplay, ScenePlan, ShotPlan,
    ContinuityState, Scene, PlanningCommitments,
)
from movie_agent.cinematic import derive_next_state, validate_transition
from .contracts import OutputErrorCode as Code, OutputIssue, ValidationReport
from .budgets import scene_shot_budget
from .cinematographer_drafts import CinematographerDraftMapper, ShotPlanDraft


class RoleOutputValidator:
    """Validate typed outputs before any state mutation or media submission."""

    def parse(self, target, raw: str, project: Project, *, scene: Scene | None = None):
        try:
            output = target.model_validate_json(raw)
        except ValidationError as error:
            return None, ValidationReport(issues=[OutputIssue(
                code=Code.MISSING_REQUIRED_FIELD if item["type"] == "missing" else Code.SCHEMA_INVALID,
                path=".".join(map(str, item["loc"])), message=item["msg"])
                for item in error.errors(include_input=False, include_url=False)])
        if isinstance(output, ShotPlanDraft):
            if scene is None:
                return None, ValidationReport(issues=[OutputIssue(code=Code.BROKEN_REFERENCE,
                    path="scene", message="ShotPlanDraft requires current Scene")])
            mapped, local_report = CinematographerDraftMapper().map(output, project, scene)
            if not local_report.valid:
                return None, local_report
            return mapped, self.validate(mapped, project, scene=scene)
        return output, self.validate(output, project, scene=scene)

    def validate(self, output, project: Project, *, scene: Scene | None = None) -> ValidationReport:
        report = ValidationReport(unverified_constraints=list(dict.fromkeys(
            project.brief.user_constraints + project.brief.must_preserve + project.brief.world_rules)))

        def issue(code, path, message):
            report.issues.append(OutputIssue(code=code, path=path, message=message))

        def retain(required, actual, path):
            for value in required:
                if value not in actual:
                    issue(Code.CONSTRAINT_VIOLATION, path, f"Must preserve verbatim: {value}")

        hard = project.brief.user_constraints + project.brief.must_preserve
        if isinstance(output, CreativeDirection):
            retain(hard, output.preserved_user_constraints, "preserved_user_constraints")
        if isinstance(output, StoryBible):
            retain(project.brief.must_preserve + project.brief.world_rules, output.immutable_facts, "immutable_facts")
        if isinstance(output, VisualBible):
            retain(project.brief.prohibited_elements, output.prohibited_visuals, "prohibited_visuals")
        if isinstance(output, PlanningCommitments):
            retain(hard, output.preserved_constraints, "preserved_constraints")
            facts = project.story_bible.immutable_facts if project.story_bible else []
            if output.immutable_facts != facts:
                issue(Code.SEMANTIC_CONFLICT, "immutable_facts", "Immutable facts must equal the canonical StoryBible list")

        # Inspect only positive creative content, not copied prohibition/constraint ledgers.
        texts = []
        if isinstance(output, CreativeDirection):
            texts = [output.premise_expansion, *output.creative_choices]
        elif isinstance(output, StoryBible):
            texts = [output.synopsis, *output.acts]
        elif isinstance(output, Screenplay):
            texts = [text for s in output.scenes for text in s.action] + [d.text for s in output.scenes for d in s.dialogue]
        elif isinstance(output, ScenePlan):
            texts = [s.purpose for s in output.scenes]
        elif isinstance(output, ShotPlan):
            texts = [text for s in output.shots for text in [s.narrative.beat, s.narrative.action_summary]]
        for prohibited in project.brief.prohibited_elements:
            for text in texts:
                pattern = r"(?<!\w)" + re.escape(prohibited) + r"(?!\w)"
                for match in re.finditer(pattern, text, re.IGNORECASE):
                    prefix = text[max(0, match.start() - 25):match.start()].lower()
                    if not re.search(r"\b(no|not|without|never|avoid|absent)\b", prefix):
                        issue(Code.CONSTRAINT_VIOLATION, "content", f"Prohibited literal appears in positive content: {prohibited}")

        characters = output.characters if isinstance(output, Screenplay) else project.characters
        locations = output.locations if isinstance(output, Screenplay) else project.locations
        props = output.props if isinstance(output, Screenplay) else project.props
        catalogs = {"character": {c.character_id for c in characters},
                    "location": {l.location_id for l in locations}, "prop": {p.prop_id for p in props}}

        def unique(values, path):
            if len(values) != len(set(values)):
                issue(Code.BROKEN_REFERENCE, path, "IDs must be unique")
            if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value) for value in values):
                issue(Code.BROKEN_REFERENCE, path, "IDs must be safe opaque identifiers using letters, digits, underscore, dot or dash")

        def refs(values, kind, path):
            for value in values:
                if value not in catalogs[kind]:
                    issue(Code.UNKNOWN_ENTITY_ID, path, f"Unknown {kind} ID: {value}")

        def state_check(state: ContinuityState, path: str):
            for kind in ("character", "location", "prop"):
                mapping = getattr(state, kind + "_states")
                refs(mapping, kind, path)
                for key, val in mapping.items():
                    if key != getattr(val, kind + "_id"):
                        issue(Code.BROKEN_REFERENCE, path, "State map key must equal entity ID")
            for val in state.character_states.values():
                refs(val.held_prop_ids, "prop", path)
            for val in state.prop_states.values():
                refs([val.holder_character_id] if val.holder_character_id else [], "character", path)

        if isinstance(output, Screenplay):
            for kind, collection in (("character", characters), ("location", locations), ("prop", props)):
                unique([getattr(e, kind + "_id") for e in collection], kind)
            unique([s.scene_id for s in output.scenes], "scenes")
            for s in output.scenes:
                refs([s.location_id], "location", s.scene_id)
                refs(s.character_ids + [d.character_id for d in s.dialogue], "character", s.scene_id)
                refs(s.prop_ids, "prop", s.scene_id)
                if any(d.character_id not in s.character_ids for d in s.dialogue):
                    missing = sorted({d.character_id for d in s.dialogue} - set(s.character_ids))
                    issue(Code.BROKEN_REFERENCE, s.scene_id + ".character_ids",
                          f"Add declared dialogue speaker IDs to character_ids: {missing}; current={s.character_ids}")
        if isinstance(output, ScenePlan):
            unique([s.scene_id for s in output.scenes], "scenes")
            if project.screenplay:
                scripts = {s.scene_id: s for s in project.screenplay.scenes}
                if set(scripts) != {s.scene_id for s in output.scenes}:
                    issue(Code.BROKEN_REFERENCE, "scenes", "Director scene IDs must exactly cover screenplay scenes")
                for s in output.scenes:
                    original = scripts.get(s.scene_id)
                    if original and (s.location_id != original.location_id or set(s.character_ids) != set(original.character_ids) or set(s.prop_ids) != set(original.prop_ids)):
                        issue(Code.BROKEN_REFERENCE, s.scene_id, "Scene entity references must match screenplay")
            if len(output.scenes) > project.brief.max_shots:
                issue(Code.CONSTRAINT_VIOLATION, "scenes", "Each scene needs at least one shot within max_shots")
            for s in output.scenes:
                refs([s.location_id], "location", s.scene_id)
                refs(s.character_ids, "character", s.scene_id)
                refs(s.prop_ids, "prop", s.scene_id)
                state_check(s.initial_state, s.scene_id + ".initial_state")
                state_check(s.expected_final_state, s.scene_id + ".expected_final_state")
                if s.shot_ids:
                    issue(Code.BROKEN_REFERENCE, s.scene_id, "Director must leave shot_ids empty until cinematography")
        if isinstance(output, ShotPlan):
            if scene is None or output.scene_id != scene.scene_id:
                issue(Code.BROKEN_REFERENCE, "scene_id", "ShotPlan must belong to current scene")
                return report
            ids = [s.shot_id for s in output.shots]
            unique(ids, "shots")
            unique([c.chain_id for c in output.continuity_chains], "continuity_chains")
            others = [s for s in project.shots if s.scene_id != scene.scene_id]
            if {c.chain_id for c in output.continuity_chains} & {s.continuity_chain_id for s in others}:
                issue(Code.BROKEN_REFERENCE, "continuity_chains", "Chain IDs must be unique across scenes")
            if set(ids) & {s.shot_id for s in others}:
                issue(Code.BROKEN_REFERENCE, "shots", "Shot IDs collide with another scene")
            try:
                allocation = scene_shot_budget(project, scene)
            except ValueError as exc:
                issue(Code.DURATION_BUDGET_ERROR, "shots", str(exc))
                return report
            if len(ids) > allocation.max_shots or len(ids) + len(others) > project.brief.max_shots:
                issue(Code.CONSTRAINT_VIOLATION, "shots", "Shot count exceeds scene/project budget")
            duration = sum(s.duration_seconds for s in output.shots)
            budget = allocation.scene_duration_budget
            if abs(duration - budget) > budget * 0.1:
                issue(Code.DURATION_BUDGET_ERROR, "shots", f"Total duration {duration:g}s must be within 10% of {budget:g}s")
            membership = [sid for c in output.continuity_chains for sid in c.shot_ids]
            if sorted(membership) != sorted(ids):
                issue(Code.BROKEN_REFERENCE, "continuity_chains", "Every shot must occur in exactly one chain")
            by_id = {s.shot_id: s for s in output.shots}
            for shot in output.shots:
                if shot.scene_id != scene.scene_id:
                    issue(Code.BROKEN_REFERENCE, shot.shot_id, "Shot references wrong scene")
                refs([p.character_id for p in shot.performances], "character", shot.shot_id)
                if any(p.character_id not in scene.character_ids for p in shot.performances):
                    issue(Code.BROKEN_REFERENCE, shot.shot_id, "Performance outside scene character set")
                if shot.retry_budget != project.brief.max_retry or shot.quality_profile != project.brief.quality_level:
                    issue(Code.CONSTRAINT_VIOLATION, shot.shot_id, "Retry and quality policy must match brief")
                for state_name in ("state_before", "expected_state_after"):
                    state = getattr(shot, state_name)
                    state_check(state, shot.shot_id)
                for anchor in (shot.frame_anchors.first_frame, shot.frame_anchors.last_frame):
                    if anchor.source_artifact_id or (anchor.source_shot_id and anchor.source_shot_id not in ids):
                        issue(Code.BROKEN_REFERENCE, shot.shot_id, "Anchor references an unavailable artifact/shot")
            for chain in output.continuity_chains:
                if not chain.shot_ids:
                    issue(Code.BROKEN_REFERENCE, chain.chain_id, "Continuity chains must contain at least one shot")
                state = chain.initial_state
                state_check(state, chain.chain_id)
                if state.model_dump() != scene.initial_state.model_dump():
                    issue(Code.SEMANTIC_CONFLICT, chain.chain_id, "Chain initial_state must equal scene initial_state")
                for index, sid in enumerate(chain.shot_ids):
                    shot = by_id.get(sid)
                    if not shot:
                        continue
                    prev = chain.shot_ids[index - 1] if index else None
                    next_id = chain.shot_ids[index + 1] if index + 1 < len(chain.shot_ids) else None
                    if (shot.previous_shot_id, shot.next_shot_id, shot.continuity_chain_id) != (prev, next_id, chain.chain_id):
                        issue(Code.BROKEN_REFERENCE, sid, "Incorrect previous/next shot or chain ID")
                    try:
                        state = derive_next_state(state, shot)
                    except ValueError as exc:
                        issue(Code.SEMANTIC_CONFLICT, sid, str(exc))
                if chain.shot_ids and chain.shot_ids[-1] in by_id:
                    end = by_id[chain.shot_ids[-1]].model_copy(update={
                        "state_before": scene.expected_final_state, "previous_shot_id": None})
                    for conflict in validate_transition(state, end):
                        issue(Code.SEMANTIC_CONFLICT, chain.chain_id, "Scene ending: " + conflict.message)
        return report
