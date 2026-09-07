"""Reference-bank state keyed only by artifact identity and immutable version."""

from __future__ import annotations

from movie_agent.domain import Scene, Shot
from movie_agent.media.contracts import (
    MediaReference,
    ReferenceBindingScope,
    ReferenceType,
)


class ReferenceBank:
    def __init__(self, references: list[MediaReference] | None = None) -> None:
        self._references = list(references or [])

    def add(self, reference: MediaReference) -> MediaReference:
        key = self._key(reference)
        duplicate = next((item for item in self._references if self._key(item) == key), None)
        if duplicate is not None:
            return duplicate.model_copy(deep=True)
        self._references.append(reference)
        return reference.model_copy(deep=True)

    @staticmethod
    def _key(reference: MediaReference) -> tuple:
        return (
            reference.reference_type,
            reference.artifact_id,
            reference.version,
            reference.binding_scope,
            reference.project_id,
            reference.entity_id,
            reference.scene_id,
            reference.shot_id,
            reference.binding_key,
            reference.purpose,
        )

    def list(
        self,
        reference_type: ReferenceType | None = None,
        *,
        entity_id: str | None = None,
        scene_id: str | None = None,
        shot_id: str | None = None,
        binding_scope: ReferenceBindingScope | None = None,
        selected_only: bool = False,
    ) -> list[MediaReference]:
        return [item.model_copy(deep=True) for item in self._references
                if (reference_type is None or item.reference_type == reference_type)
                and (entity_id is None or item.entity_id == entity_id)
                and (scene_id is None or item.scene_id == scene_id)
                and (shot_id is None or item.shot_id == shot_id)
                and (binding_scope is None or item.binding_scope == binding_scope)
                and (not selected_only or item.selected)]

    def all(self) -> list[MediaReference]:
        return [item.model_copy(deep=True) for item in self._references]

    def unbind(self, reference_id: str) -> MediaReference:
        target = next((item for item in self._references if item.reference_id == reference_id), None)
        if target is None:
            raise KeyError(reference_id)
        self._references = [item for item in self._references if item.reference_id != reference_id]
        return target.model_copy(update={"selected": False}, deep=True)

    def select(self, artifact_id: str, version: int | None = None) -> MediaReference:
        target = next((item for item in self._references
                       if item.artifact_id == artifact_id
                       and (version is None or item.version == version)), None)
        if target is None:
            raise KeyError(artifact_id)
        found = target.model_copy(update={"selected": True})
        updated = [
            (found if item is target else item.model_copy(update={"selected": False})
             if item.reference_type == target.reference_type
             and item.entity_id == target.entity_id and item.shot_id == target.shot_id
             else item)
            for item in self._references
        ]
        self._references = updated
        return found.model_copy(deep=True)


class ReferenceResolver:
    """Select only relevant references with stable scope precedence and de-duplication."""

    _PRECEDENCE = {
        ReferenceBindingScope.SHOT: 0,
        ReferenceBindingScope.FRAME: 0,
        ReferenceBindingScope.SCENE: 1,
        ReferenceBindingScope.ENTITY: 2,
        ReferenceBindingScope.CREATIVE_INPUT: 3,
        ReferenceBindingScope.PROJECT: 4,
        None: 5,
    }

    def __init__(self, *, max_references: int = 8) -> None:
        self.max_references = max_references

    def resolve(
        self,
        references: list[MediaReference],
        *,
        project_id: str,
        shot: Shot,
        scene: Scene | None,
    ) -> list[MediaReference]:
        entity_ids = {item.character_id for item in shot.performances}
        if scene:
            entity_ids.update(scene.character_ids)
            entity_ids.update(scene.prop_ids)
            entity_ids.add(scene.location_id)
        eligible = [item for item in references if item.selected and (
            item.project_id in {None, project_id}
        ) and (
            item.binding_scope in {None, ReferenceBindingScope.PROJECT, ReferenceBindingScope.CREATIVE_INPUT}
            or item.binding_scope == ReferenceBindingScope.SHOT and item.shot_id == shot.shot_id
            or item.binding_scope == ReferenceBindingScope.FRAME and item.shot_id == shot.shot_id
            or item.binding_scope == ReferenceBindingScope.SCENE and item.scene_id == shot.scene_id
            or item.binding_scope == ReferenceBindingScope.ENTITY and item.entity_id in entity_ids
        )]
        eligible.sort(key=lambda item: (self._PRECEDENCE[item.binding_scope], item.reference_id))
        result: list[MediaReference] = []
        seen: set[tuple[str, int | None, ReferenceType]] = set()
        for item in eligible:
            key = (item.artifact_id, item.version, item.reference_type)
            if key in seen:
                continue
            seen.add(key)
            result.append(item.model_copy(deep=True))
            if len(result) == self.max_references:
                break
        return result
