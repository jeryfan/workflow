from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AppImportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    app_id: str | None = None
    yaml_content: str | None = None
    yaml_url: str | None = None
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    icon_background: str | None = None
    icon_type: str | None = None
    package_url: str | None = None
    template_id: str | None = None
    version_id: str | None = None


class CreateAppPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    mode: str
    description: str | None = None
    icon: str | None = None
    icon_type: str | None = None
    icon_background: str | None = None


class UpdateAppPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    icon: str | None = None
    icon_type: str | None = None
    icon_background: str | None = None
    use_icon_as_answer_icon: bool | None = None
    max_active_requests: int | None = None


class CopyAppPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    icon: str | None = None
    icon_type: str | None = None
    icon_background: str | None = None


class SiteEnablePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enable_site: bool
