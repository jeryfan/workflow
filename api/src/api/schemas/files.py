from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RemoteFilePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
