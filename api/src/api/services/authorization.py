from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from api.configs import PROJECT_ROOT
from api.models.app import App

from .base_service import BaseService

if TYPE_CHECKING:
    from api.schemas.auth import Principal

APP_READ = "app.acl.view_layout"
APP_RUN = "app.acl.test_and_run"
APP_EDIT = "app.acl.edit"
APP_IMPORT_EXPORT = "app.acl.import_export_dsl"
APP_PUBLISH = "app.acl.release_and_version"
APP_DELETE = "app.acl.delete"
APP_CREATE_AND_MANAGEMENT = "app.create_and_management"


class AppNotFound(Exception):
    pass


class AccessDenied(Exception):
    pass


@dataclass(frozen=True, slots=True)
class WorkflowResource:
    id: str
    name: str
    created_at: int
    maintainer_id: str | None
    created_by_id: str | None
    updated_by_id: str | None


@dataclass(frozen=True, slots=True)
class Authorization:
    resource: WorkflowResource
    role: str
    permission_keys: frozenset[str]


class RoleRegistry:
    def __init__(self, path: Path) -> None:
        contract = json.loads(path.read_text(encoding="utf-8"))
        self._roles = contract["roles"]

    def account_keys(self, role: str) -> frozenset[str]:
        return frozenset(self._roles[role]["account"])

    def app_keys(self, role: str) -> frozenset[str]:
        return frozenset(self._roles[role]["app"])


class AuthorizationService(BaseService):
    def __init__(self, db) -> None:
        super().__init__(db)
        self.roles = RoleRegistry(PROJECT_ROOT / "contracts" / "permissions.json")

    async def authorization(self, principal: Principal, app_id: str) -> Authorization:
        app = await self.db.get(App, app_id)
        if app is None:
            raise AppNotFound
        resource = WorkflowResource(
            id=app.id,
            name=app.name,
            created_at=app.created_at,
            maintainer_id=app.maintainer_id,
            created_by_id=app.created_by_id,
            updated_by_id=app.updated_by_id,
        )
        role = principal.role.value
        return Authorization(resource=resource, role=role, permission_keys=self.roles.app_keys(role))

    async def require(self, principal: Principal, app_id: str, permission: str) -> Authorization:
        authorization = await self.authorization(principal, app_id)
        if permission not in authorization.permission_keys:
            raise AccessDenied(permission)
        return authorization
