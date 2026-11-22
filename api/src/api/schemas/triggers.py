from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class TriggerEnablePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger_id: str
    enable_trigger: bool
