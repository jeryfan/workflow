from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class StringSource(BaseModel):
    type: Literal["constant", "variable"] = "constant"
    selector: list[str] = Field(default_factory=list)
    value: str = ""


class StringListSource(BaseModel):
    type: Literal["constant", "variable"] = "constant"
    selector: list[str] = Field(default_factory=list)
    value: list[str] = Field(default_factory=list)


class ParagraphInput(BaseModel):
    type: Literal["paragraph"]
    output_variable_name: str
    default: StringSource | None = None


class SelectInput(BaseModel):
    type: Literal["select"]
    output_variable_name: str
    option_source: StringListSource


class FileInput(BaseModel):
    type: Literal["file"]
    output_variable_name: str
    allowed_file_types: list[str] = Field(default_factory=list)
    allowed_file_extensions: list[str] = Field(default_factory=list)
    allowed_file_upload_methods: list[str] = Field(default_factory=list)


class FileListInput(BaseModel):
    type: Literal["file-list"]
    output_variable_name: str
    allowed_file_types: list[str] = Field(default_factory=list)
    allowed_file_extensions: list[str] = Field(default_factory=list)
    allowed_file_upload_methods: list[str] = Field(default_factory=list)
    number_limits: int = Field(default=5, ge=1, le=20)


FormInput = ParagraphInput | SelectInput | FileInput | FileListInput


class UserAction(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=20)
    title: str = Field(min_length=1, max_length=100)
    button_style: Literal["primary", "default", "accent", "ghost"] = "default"


class DeliveryMethod(BaseModel):
    id: str = Field(default="webapp")
    type: Literal["webapp"]
    enabled: bool = True


class HumanInputConfig(BaseModel):
    title: str = ""
    form_content: str = Field(default="", max_length=100_000)
    inputs: list[FormInput] = Field(default_factory=list)
    resolved_default_values: dict[str, object] = Field(default_factory=dict)
    user_actions: list[UserAction] = Field(min_length=1)
    timeout: int = Field(default=3, ge=1, le=3650)
    timeout_unit: Literal["hour", "day"] = "day"
    delivery_methods: list[DeliveryMethod] = Field(min_length=1)

    @field_validator("inputs")
    @classmethod
    def unique_input_names(cls, inputs: list[FormInput]) -> list[FormInput]:
        names = [item.output_variable_name for item in inputs]
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in names):
            raise ValueError("Input output variable names must be valid identifiers")
        if len(names) != len(set(names)):
            raise ValueError("Input output variable names must be unique")
        return inputs

    @field_validator("user_actions")
    @classmethod
    def unique_actions(cls, actions: list[UserAction]) -> list[UserAction]:
        identifiers = [action.id for action in actions]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("User action ids must be unique")
        if "__timeout" in identifiers:
            raise ValueError("__timeout is reserved")
        return actions

    @model_validator(mode="after")
    def enabled_delivery(self) -> HumanInputConfig:
        if not any(method.enabled for method in self.delivery_methods):
            raise ValueError("At least one delivery method must be enabled")
        return self


class HumanInputSubmitPayload(BaseModel):
    inputs: dict[str, object] = Field(default_factory=dict)
    action: str = Field(min_length=1, max_length=20)
