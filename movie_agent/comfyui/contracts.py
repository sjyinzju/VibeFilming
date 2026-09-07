"""Typed, versioned contracts for ComfyUI API-format workflow adapters."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import ConfigDict, Field, model_validator

from movie_agent.domain.base import ContractModel, JSONValue
from movie_agent.media.contracts import MediaModality, VideoGenerationMode


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude_defaults=True
        )
    raise TypeError(f"value is not JSON serializable: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize a JSON-compatible value identically on every platform."""

    return json.dumps(
        value,
        default=_json_value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_workflow_hash(api_workflow: Any) -> str:
    return hashlib.sha256(canonical_json(api_workflow).encode("utf-8")).hexdigest()


class BindingValueType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ASSET = "asset"
    ASSET_LIST = "asset_list"
    JSON = "json"


class BindingTransform(StrEnum):
    IDENTITY = "identity"
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"


class ComfyUIOutputSource(StrEnum):
    REMOTE_FILE = "remote_file"
    MUXED_AUDIO = "muxed_audio"


class ComfyUIAPINode(ContractModel):
    model_config = ConfigDict(populate_by_name=True)

    class_type: str = Field(min_length=1)
    inputs: dict[str, JSONValue]
    meta: dict[str, JSONValue] = Field(default_factory=dict, alias="_meta")


class ComfyUIOutputDeclaration(ContractModel):
    node_id: str = Field(min_length=1)
    modality: MediaModality
    purpose: str = Field(min_length=1)
    history_key: str = Field(min_length=1)
    mime_type: str = Field(pattern=r"^(image|video|audio)/[A-Za-z0-9.+-]+$")
    extension: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.+-]{0,15}$")
    primary: bool = False
    required: bool = True
    codec: str | None = None
    source: ComfyUIOutputSource = ComfyUIOutputSource.REMOTE_FILE


class ComfyUIWorkflowTemplate(ContractModel):
    """Immutable API-format workflow; GUI workflow JSON is not accepted here."""

    model_config = ConfigDict(frozen=True)

    template_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    modality: MediaModality
    generation_mode: VideoGenerationMode
    api_workflow: dict[str, ComfyUIAPINode]
    template_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    required_capabilities: list[str] = Field(default_factory=list)
    supported_capabilities: list[str] = Field(default_factory=list)
    required_semantic_slots: list[str] = Field(default_factory=list)
    outputs: list[ComfyUIOutputDeclaration]
    display_metadata: dict[str, JSONValue] = Field(default_factory=dict)
    node_metadata: dict[str, dict[str, JSONValue]] = Field(default_factory=dict)
    edge_metadata: list[dict[str, JSONValue]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_template(self) -> "ComfyUIWorkflowTemplate":
        if not self.api_workflow:
            raise ValueError("ComfyUI API workflow cannot be empty")
        if self.template_hash != compute_workflow_hash(self.api_workflow):
            raise ValueError("ComfyUI workflow template hash does not match API workflow")
        output_targets = [(item.node_id, item.history_key, item.source) for item in self.outputs]
        if len(output_targets) != len(set(output_targets)):
            raise ValueError("ComfyUI workflow output node/history targets must be unique")
        output_ids = [item.node_id for item in self.outputs]
        missing = [item for item in output_ids if item not in self.api_workflow]
        if missing:
            raise ValueError(f"ComfyUI workflow output node does not exist: {missing[0]}")
        if sum(item.primary for item in self.outputs) != 1:
            raise ValueError("ComfyUI workflow must declare exactly one primary output")
        if not next(item for item in self.outputs if item.primary).modality == self.modality:
            raise ValueError("ComfyUI primary output modality must match the template")
        return self


class WorkflowBinding(ContractModel):
    semantic_slot: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    node_id: str = Field(min_length=1)
    input_name: str = Field(min_length=1)
    value_type: BindingValueType
    required: bool = False
    transform: BindingTransform = BindingTransform.IDENTITY
    expected_class_type: str | None = None


class WorkflowBindingManifest(ContractModel):
    model_config = ConfigDict(frozen=True)

    manifest_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    template_version: str = Field(min_length=1)
    bindings: list[WorkflowBinding]
    inline_negative_prompt: bool = False

    @model_validator(mode="after")
    def validate_unique_bindings(self) -> "WorkflowBindingManifest":
        slots = [item.semantic_slot for item in self.bindings]
        targets = [(item.node_id, item.input_name) for item in self.bindings]
        if len(slots) != len(set(slots)):
            raise ValueError("duplicate ComfyUI semantic binding")
        if len(targets) != len(set(targets)):
            raise ValueError("conflicting ComfyUI node input binding")
        return self


class ComfyUIInputAsset(ContractModel):
    semantic_slot: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    artifact_id: str = Field(min_length=1)
    artifact_version: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    filename: str = Field(min_length=1)
    subfolder: str = ""
    type: str = "input"
    mime_type: str | None = None

    @property
    def input_name(self) -> str:
        return f"{self.subfolder.rstrip('/')}/{self.filename}" if self.subfolder else self.filename


class ComfyUIExecutionSpec(ContractModel):
    prompt: dict[str, dict[str, JSONValue]]
    input_assets: list[ComfyUIInputAsset]
    expected_outputs: list[ComfyUIOutputDeclaration]
    template_id: str
    template_version: str
    template_hash: str
    binding_manifest_id: str
    binding_manifest_version: str
    model_profile: str
    execution_metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @property
    def prompt_json(self) -> str:
        return canonical_json(self.prompt)


class ComfyUIWorkflowProfile(ContractModel):
    model_config = ConfigDict(frozen=True)

    profile_id: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    template_version: str = Field(min_length=1)
    binding_manifest_id: str = Field(min_length=1)
    binding_manifest_version: str = Field(min_length=1)
    model_profile: str = Field(min_length=1)
