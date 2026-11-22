from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SetupPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    name: str
    password: str
    language: str = "en-US"


class LoginPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str
    remember_me: bool = False
