"""Reference-bank state keyed only by artifact identity and immutable version."""

from __future__ import annotations

from movie_agent.media.contracts import MediaReference, ReferenceType


class ReferenceBank:
    def __init__(self, references: list[MediaReference] | None = None) -> None:
        self._references = list(references or [])

    def add(self, reference: MediaReference) -> MediaReference:
        key = (reference.reference_type, reference.artifact_id, reference.version,
               reference.entity_id, reference.shot_id)
        if any((item.reference_type, item.artifact_id, item.version,
                item.entity_id, item.shot_id) == key for item in self._references):
            raise ValueError("duplicate media reference binding")
        self._references.append(reference)
        return reference

    def list(
        self,
        reference_type: ReferenceType | None = None,
        *,
        entity_id: str | None = None,
        shot_id: str | None = None,
        selected_only: bool = False,
    ) -> list[MediaReference]:
        return [item.model_copy(deep=True) for item in self._references
                if (reference_type is None or item.reference_type == reference_type)
                and (entity_id is None or item.entity_id == entity_id)
                and (shot_id is None or item.shot_id == shot_id)
                and (not selected_only or item.selected)]

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
