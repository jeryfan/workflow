from __future__ import annotations

from typing import TYPE_CHECKING, Any

from api.config import get_settings
from api.db.store import Store
from api.models.entities import AppRecord, CopyAppCommand, CreateAppCommand, UpdateAppCommand
from api.services.authorization import (
    APP_CREATE_AND_MANAGEMENT,
    APP_DELETE,
    APP_EDIT,
    APP_READ,
    AccessDenied,
    AppNotFound,
    AuthorizationService,
)

if TYPE_CHECKING:
    from api.services.auth import Principal
    from api.services.files import FileService


class InvalidApp(ValueError):
    pass


class AppService:
    def __init__(self, store: Store, access: AuthorizationService, files: FileService) -> None:
        self.store = store
        self.access = access
        self.files = files

    def require_create(self, principal: Principal) -> None:
        if APP_CREATE_AND_MANAGEMENT not in self.access.roles.account_keys(principal.role):
            raise AccessDenied(APP_CREATE_AND_MANAGEMENT)

    def get(self, principal: Principal, app_id: str) -> dict[str, Any]:
        authorization = self.access.require(principal, app_id, APP_READ)
        record = self.store.get_app_record(app_id)
        if record is None:
            raise AppNotFound
        return self.app_json(record, authorization.permission_keys)

    def list(
        self,
        principal: Principal,
        *,
        page: int,
        limit: int,
        name: str | None,
        mode: str | None,
        sort_by: str,
        creator_ids: list[str] | None,
        is_created_by_me: bool,
    ) -> dict[str, Any]:
        selected_creator_ids = [principal.account_id] if is_created_by_me else creator_ids
        records = self.store.list_app_records(
            name=name,
            mode=mode,
            sort_by=sort_by,
            creator_ids=selected_creator_ids,
        )
        visible = []
        for record in records:
            try:
                authorization = self.access.authorization(principal, record.id)
            except (AccessDenied, AppNotFound):
                continue
            if APP_READ in authorization.permission_keys:
                visible.append((record, authorization.permission_keys))
        total = len(visible)
        start = (page - 1) * limit
        items = visible[start : start + limit]
        return {
            "data": [self.app_json(record, keys, detail=False) for record, keys in items],
            "page": page,
            "limit": limit,
            "total": total,
            "has_more": start + len(items) < total,
        }

    def create(self, principal: Principal, command: CreateAppCommand) -> dict[str, Any]:
        self.require_create(principal)
        name = command.name.strip()
        if not name:
            raise InvalidApp("App name must not be empty")
        if command.mode != "workflow":
            raise InvalidApp("Only workflow apps are supported")
        record = self.store.create_app(command)
        return self.get(principal, record.id)

    def update(self, principal: Principal, command: UpdateAppCommand) -> dict[str, Any]:
        self.access.require(principal, command.app_id, APP_EDIT)
        if command.name is not None and not command.name.strip():
            raise InvalidApp("App name must not be empty")
        record = self.store.update_app(command)
        if record is None:
            raise AppNotFound
        return self.get(principal, command.app_id)

    def copy(self, principal: Principal, command: CopyAppCommand) -> dict[str, Any]:
        self.access.require(principal, command.source_app_id, APP_READ)
        self.require_create(principal)
        record = self.store.copy_app(command)
        if record is None:
            raise AppNotFound
        return self.get(principal, record.id)

    def delete(self, principal: Principal, app_id: str) -> None:
        self.access.require(principal, app_id, APP_DELETE)
        uploaded_files = self.store.list_uploaded_files_for_app(app_id)
        if not self.store.delete_app(app_id):
            raise AppNotFound
        self.files.delete_content(uploaded_files)

    def app_json(self, record: AppRecord, permission_keys, *, detail: bool = True) -> dict[str, Any]:
        published = self.store.get_published(record.id)
        site = self.store.get_site(record.id)
        result = {
            "id": record.id,
            "app_id": record.id,
            "name": record.name,
            "description": record.description,
            "mode": record.mode,
            "icon": record.icon,
            "icon_type": record.icon_type,
            "icon_background": record.icon_background,
            "icon_url": None,
            "author_name": record.author_name,
            "create_user_name": record.author_name,
            "created_by": record.created_by_id,
            "created_at": record.created_at,
            "updated_by": record.updated_by_id,
            "updated_at": record.updated_at,
            "maintainer": record.maintainer_id,
            "permission_keys": sorted(permission_keys),
            "tags": [],
            "is_starred": False,
            "has_draft_trigger": False,
            "use_icon_as_answer_icon": record.use_icon_as_answer_icon,
            "workflow": {"id": published.id} if published is not None else None,
            "workflow_type": None,
            "workflow_kind": None,
        }
        if detail:
            result.update(
                {
                    "access_mode": None,
                    "api_base_url": get_settings().public_api_url,
                    "bound_agent_id": None,
                    "deleted_tools": [],
                    "enable_site": site.enable_site if site is not None else False,
                    "enable_api": False,
                    "max_active_requests": None,
                    "model_config": None,
                    "site": self.site_json(site) if site is not None else None,
                    "tracing": None,
                }
            )
        return result

    @staticmethod
    def site_json(site) -> dict[str, Any]:
        return {
            "access_token": site.access_token,
            "app_base_url": get_settings().public_web_url.rstrip("/"),
            "title": "",
            "icon_type": None,
            "icon": None,
            "icon_background": None,
            "icon_url": None,
            "description": "",
            "default_language": "zh-CN",
            "show_workflow_steps": True,
            "use_icon_as_answer_icon": False,
        }
