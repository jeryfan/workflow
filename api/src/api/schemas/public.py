from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class PublicWorkflowRunPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs: dict[str, Any]
    files: list[dict[str, Any]] | None = None
    response_mode: str = "streaming"
