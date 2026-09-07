"""Immutable registry for ComfyUI templates, bindings, and selectable profiles."""

from __future__ import annotations

from .compiler import validate_template_binding
from .contracts import (
    ComfyUIWorkflowProfile,
    ComfyUIWorkflowTemplate,
    WorkflowBindingManifest,
    compute_workflow_hash,
)
from .profiles import MINIMAX_H3_FL2VA_PROFILE_ID, minimax_h3_fl2va_components


class ComfyUIWorkflowRegistry:
    def __init__(self, *, include_builtin_profiles: bool = True) -> None:
        self._templates: dict[tuple[str, str], ComfyUIWorkflowTemplate] = {}
        self._manifests: dict[tuple[str, str], WorkflowBindingManifest] = {}
        self._profiles: dict[str, ComfyUIWorkflowProfile] = {}
        if include_builtin_profiles:
            profile, template, manifest = minimax_h3_fl2va_components()
            self.register_template(template)
            self.register_manifest(manifest)
            self.register_profile(profile)

    def register_template(self, template: ComfyUIWorkflowTemplate) -> None:
        key = (template.template_id, template.version)
        if key in self._templates:
            raise ValueError(f"ComfyUI workflow template version is immutable: {key[0]}@{key[1]}")
        self._templates[key] = template.model_copy(deep=True)

    def register_manifest(self, manifest: WorkflowBindingManifest) -> None:
        key = (manifest.manifest_id, manifest.version)
        if key in self._manifests:
            raise ValueError(f"ComfyUI binding manifest version is immutable: {key[0]}@{key[1]}")
        self._manifests[key] = manifest.model_copy(deep=True)

    def register_profile(self, profile: ComfyUIWorkflowProfile) -> None:
        if profile.profile_id in self._profiles:
            raise ValueError(f"duplicate ComfyUI workflow profile: {profile.profile_id}")
        template = self._templates[(profile.template_id, profile.template_version)]
        manifest = self._manifests[(profile.binding_manifest_id, profile.binding_manifest_version)]
        validate_template_binding(template, manifest)
        self._profiles[profile.profile_id] = profile.model_copy(deep=True)

    def resolve(
        self, profile_id: str
    ) -> tuple[ComfyUIWorkflowProfile, ComfyUIWorkflowTemplate, WorkflowBindingManifest]:
        try:
            profile = self._profiles[profile_id]
            template = self._templates[(profile.template_id, profile.template_version)]
            if compute_workflow_hash(template.api_workflow) != template.template_hash:
                raise ValueError(
                    f"registered ComfyUI workflow template was mutated: {template.template_id}@{template.version}"
                )
            return (
                profile.model_copy(deep=True),
                template.model_copy(deep=True),
                self._manifests[(
                    profile.binding_manifest_id, profile.binding_manifest_version
                )].model_copy(deep=True),
            )
        except KeyError as error:
            raise LookupError(f"ComfyUI workflow profile is not installed: {profile_id}") from error
