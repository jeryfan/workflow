from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select

from api.config import PROJECT_ROOT
from api.db.store import Store

if TYPE_CHECKING:
    from api.services.auth import Principal

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


class AuthorizationService:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.roles = RoleRegistry(PROJECT_ROOT / "contracts" / "permissions.json")

    def authorization(self, principal: Principal, app_id: str) -> Authorization:
        with self.store.engine.connect() as connection:
            row = connection.execute(
                select(
                    self.store.apps.c.id,
                    self.store.apps.c.name,
                    self.store.apps.c.created_at,
                    self.store.apps.c.maintainer_id,
                    self.store.apps.c.created_by_id,
                    self.store.apps.c.updated_by_id,
                ).where(self.store.apps.c.id == app_id)
            ).first()
        if row is None:
            raise AppNotFound
        resource = WorkflowResource(
            id=row.id,
            name=row.name,
            created_at=row.created_at,
            maintainer_id=row.maintainer_id,
            created_by_id=row.created_by_id,
            updated_by_id=row.updated_by_id,
        )
        role = principal.role.value
        return Authorization(resource=resource, role=role, permission_keys=self.roles.app_keys(role))

    def require(self, principal: Principal, app_id: str, permission: str) -> Authorization:
        authorization = self.authorization(principal, app_id)
        if permission not in authorization.permission_keys:
            raise AccessDenied(permission)
        return authorization
