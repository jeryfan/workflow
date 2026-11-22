from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkflowToolParameterPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    form: str = "llm"
    required: bool = False
    type: str | None = None


class WorkflowToolPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    icon: dict[str, Any]
    description: str = Field(default="", max_length=4000)
    parameters: list[WorkflowToolParameterPayload] = Field(default_factory=list, max_length=100)
    labels: list[str] = Field(default_factory=list, max_length=20)
    privacy_policy: str = Field(default="", max_length=2000)


class WorkflowToolCreatePayload(WorkflowToolPayload):
    workflow_app_id: str


class WorkflowToolUpdatePayload(WorkflowToolPayload):
    workflow_tool_id: str


class WorkflowToolDeletePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_tool_id: str
