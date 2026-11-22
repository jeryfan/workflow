from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from api.models.entities import Actor


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


class AccountRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    NORMAL = "normal"


@dataclass(frozen=True, slots=True)
class Principal:
    account_id: str
    email: str
    name: str
    role: AccountRole

    @property
    def actor(self) -> Actor:
        return Actor(id=self.account_id, name=self.name, email=self.email)


@dataclass(frozen=True, slots=True)
class SetupCommand:
    email: str
    name: str
    password: str
    language: str = "en-US"


@dataclass(frozen=True, slots=True)
class LoginCommand:
    email: str
    password: str
    client_ip: str


@dataclass(frozen=True, slots=True)
class ProvisionMemberCommand:
    email: str
    name: str
    password: str
    role: AccountRole
