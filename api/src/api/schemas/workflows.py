from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EnvironmentVariablePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    name: str
    value_type: str
    value: Any = None
    description: str = ""


class EnvironmentVariablePatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment_variables: list[EnvironmentVariablePayload] = Field(default_factory=list)
    deleted_environment_variable_ids: list[str] = Field(default_factory=list)


class EnvironmentVariableUpdatePayload(EnvironmentVariablePatchPayload):
    patch: bool = False


class SyncDraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    graph: dict[str, Any]
    features: dict[str, Any]
    hash: str | None = None
    force: bool = False
    is_collaborative: bool = Field(default=False, validation_alias="_is_collaborative")
    environment_variable_patch: EnvironmentVariablePatchPayload | None = None
    conversation_variables: list[dict[str, Any]] = Field(default_factory=list)


class PublishWorkflowPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marked_name: str | None = Field(default=None, max_length=20)
    marked_comment: str | None = Field(default=None, max_length=100)


class WorkflowUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marked_name: str | None = Field(default=None, max_length=20)
    marked_comment: str | None = Field(default=None, max_length=100)


class DraftRunPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs: dict[str, Any]
    files: list[dict[str, Any]] | None = None
