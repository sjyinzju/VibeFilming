"""Deterministic role-specific context projection and hashing."""

import hashlib
import json
from movie_agent.domain import Project, Scene
from .contracts import RoleContext, RoleDefinition
from .budgets import scene_shot_budget
from .cinematographer_drafts import CanonicalChainContext


def content_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


class ContextBuilder:
    """Allowlist domain projections; jobs, provenance and serving config are inaccessible."""

    def build(self, definition: RoleDefinition, project: Project, *, scene: Scene | None = None) -> RoleContext:
        brief = project.brief.model_dump(mode="json")
        payload = {"brief": {key: brief[key] for key in definition.context_policy.brief_fields}}
        ids = [project.project_id + ":brief"]
        for source in definition.context_policy.sources:
            if source == "scene":
                if scene is None:
                    raise ValueError("cinematography requires a scene")
                value = scene
            elif source == "continuity":
                value = scene.initial_state if scene else None
            elif source == "screenplay_scene":
                value = next((s for s in project.screenplay.scenes if s.scene_id == scene.scene_id), None) if project.screenplay and scene else None
            else:
                value = getattr(project, source)
            if isinstance(value, list):
                if scene and source in {"characters", "props", "locations"}:
                    key = {"characters": "character_id", "props": "prop_id", "locations": "location_id"}[source]
                    allowed = scene.character_ids if source == "characters" else scene.prop_ids if source == "props" else [scene.location_id]
                    value = [item for item in value if getattr(item, key) in allowed]
                payload[source] = [item.model_dump(mode="json") for item in value]
                for item in value:
                    ids.extend(str(v) for k, v in item.model_dump().items() if k.endswith("_id") and isinstance(v, str))
            else:
                payload[source] = value.model_dump(mode="json") if value else None
                if value:
                    ids.append(project.project_id + ":" + source)
                    ids.extend(str(v) for k, v in value.model_dump().items()
                               if k.endswith("_id") and isinstance(v, str))
        if scene:
            # Canonical global constraints remain; other scenes' story arcs/history do not.
            if payload.get("story_bible"):
                payload["story_bible"] = {k: payload["story_bible"][k]
                    for k in ("immutable_facts", "world_facts", "themes")}
            for source in ("characters", "locations", "props"):
                for entity in payload.get(source, []):
                    entity.pop("reference_artifact_ids", None)
            budget = scene_shot_budget(project, scene)
            payload["scene"] = {key: value for key, value in payload["scene"].items()
                                if key not in {"initial_state", "expected_final_state", "shot_ids"}}
            canonical = CanonicalChainContext(scene_id=scene.scene_id,
                initial_state=scene.initial_state, expected_final_state=scene.expected_final_state,
                canonical_character_ids=sorted(scene.initial_state.character_states),
                canonical_location_ids=sorted(scene.initial_state.location_states),
                canonical_prop_ids=sorted(scene.initial_state.prop_states))
            payload["canonical_chain_context"] = canonical.model_dump(mode="json")
            payload["shot_budget"] = budget.model_dump(mode="json")
            payload["scene_duration_budget"] = budget.scene_duration_budget
            payload["scene_shot_budget"] = budget.max_shots
            ids.append(scene.scene_id)
        return RoleContext(payload=payload, source_ids=sorted(set(ids)), context_hash=content_hash(payload),
                           context_version="3" if scene else "1")
