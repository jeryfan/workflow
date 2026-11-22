from __future__ import annotations

import json
import secrets
import time
from dataclasses import asdict
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import delete, func, select

from api.configs import app_config
from api.models.account import Account
from api.models.app import App, AppSite
from api.models.entities import AppRecord, CopyAppCommand, CreateAppCommand, UpdateAppCommand
from api.models.execution import NodeExecution, WorkflowRun
from api.models.uploaded_file import UploadedFile
from api.models.workflow import WorkflowDraft
from api.models.workflow_tool import WorkflowTool
from api.models.workflow_trigger import WorkflowTrigger

from .authorization import (
    APP_CREATE_AND_MANAGEMENT,
    APP_DELETE,
    APP_EDIT,
    APP_PUBLISH,
    APP_READ,
    AccessDenied,
    AppNotFound,
    AuthorizationService,
)
from .base_service import BaseService

if TYPE_CHECKING:
    from api.schemas.auth import Principal

    from .files import FileService


class InvalidApp(ValueError):
    pass


class AppService(BaseService):
    def __init__(self, db, access: AuthorizationService, files: FileService) -> None:
        super().__init__(db)
        self.access = access
        self.files = files

    def require_create(self, principal: Principal) -> None:
        if APP_CREATE_AND_MANAGEMENT not in self.access.roles.account_keys(principal.role):
            raise AccessDenied(APP_CREATE_AND_MANAGEMENT)

    async def _record(self, app_id: str) -> AppRecord | None:
        result = await self.db.execute(
            select(App, Account.name.label("author_name"))
            .outerjoin(Account, Account.id == App.created_by_id)
            .where(App.id == app_id)
        )
        row = result.first()
        return self._from_model(*row) if row is not None else None

    @staticmethod
    def _from_model(app: App, author_name: str | None = None) -> AppRecord:
        return AppRecord(
            id=app.id,
            name=app.name,
            mode=app.mode,
            created_at=app.created_at,
            updated_at=app.updated_at if app.updated_at is not None else app.created_at,
            maintainer_id=app.maintainer_id,
            created_by_id=app.created_by_id,
            updated_by_id=app.updated_by_id,
            published_workflow_id=app.published_workflow_id,
            author_name=author_name,
            description=app.description or "",
            icon=app.icon,
            icon_type=app.icon_type,
            icon_background=app.icon_background,
            use_icon_as_answer_icon=bool(app.use_icon_as_answer_icon),
        )

    async def get(self, principal: Principal, app_id: str) -> dict[str, Any]:
        authorization = await self.access.require(principal, app_id, APP_READ)
        record = await self._record(app_id)
        if record is None:
            raise AppNotFound
        site = await self.db.get(AppSite, app_id)
        return self.app_json(record, authorization.permission_keys, site=site)

    async def list(
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
        statement = select(App, Account.name.label("author_name")).outerjoin(Account, Account.id == App.created_by_id)
        selected_creator_ids = [principal.account_id] if is_created_by_me else creator_ids
        if name:
            statement = statement.where(func.lower(App.name).contains(name.lower()))
        if mode and mode != "all":
            statement = statement.where(App.mode == mode)
        if selected_creator_ids:
            statement = statement.where(App.created_by_id.in_(selected_creator_ids))
        if sort_by == "earliest_created":
            statement = statement.order_by(App.created_at.asc(), App.id.asc())
        elif sort_by == "recently_created":
            statement = statement.order_by(App.created_at.desc(), App.id.desc())
        else:
            statement = statement.order_by(App.updated_at.desc(), App.id.desc())
        rows = (await self.db.execute(statement)).all()
        records = [self._from_model(app, author_name) for app, author_name in rows]
        keys = self.access.roles.app_keys(principal.role)
        total = len(records)
        start = (page - 1) * limit
        items = records[start : start + limit]
        return {
            "data": [self.app_json(record, keys, detail=False) for record in items],
            "page": page,
            "limit": limit,
            "total": total,
            "has_more": start + len(items) < total,
        }

    async def create(self, principal: Principal, command: CreateAppCommand) -> dict[str, Any]:
        self.require_create(principal)
        if not command.name.strip():
            raise InvalidApp("App name must not be empty")
        if command.mode != "workflow":
            raise InvalidApp("Only workflow apps are supported")
        now = int(time.time())
        app = App(
            id=str(uuid4()),
            name=command.name,
            mode=command.mode,
            description=command.description,
            icon=command.icon,
            icon_type=command.icon_type,
            icon_background=command.icon_background,
            use_icon_as_answer_icon=0,
            created_at=now,
            updated_at=now,
            maintainer_id=command.actor.id,
            created_by_id=command.actor.id,
            updated_by_id=command.actor.id,
        )
        self.db.add(app)
        await self.db.commit()
        return await self.get(principal, app.id)

    async def update(self, principal: Principal, command: UpdateAppCommand) -> dict[str, Any]:
        await self.access.require(principal, command.app_id, APP_EDIT)
        if command.name is not None and not command.name.strip():
            raise InvalidApp("App name must not be empty")
        app = await self.db.get(App, command.app_id)
        if app is None:
            raise AppNotFound
        for field in (
            "name",
            "description",
            "icon",
            "icon_type",
            "icon_background",
            "use_icon_as_answer_icon",
        ):
            value = getattr(command, field)
            if value is not None:
                setattr(app, field, int(value) if field == "use_icon_as_answer_icon" else value)
        app.updated_at = int(time.time())
        app.updated_by_id = command.actor.id
        await self.db.commit()
        return await self.get(principal, command.app_id)

    async def copy(self, principal: Principal, command: CopyAppCommand) -> dict[str, Any]:
        await self.access.require(principal, command.source_app_id, APP_READ)
        self.require_create(principal)
        source = await self._record(command.source_app_id)
        if source is None:
            raise AppNotFound
        now = int(time.time())
        app_id = str(uuid4())
        self.db.add(
            App(
                id=app_id,
                name=command.name or f"{source.name} copy",
                mode=source.mode,
                description=command.description if command.description is not None else source.description,
                icon=command.icon if command.icon is not None else source.icon,
                icon_type=command.icon_type if command.icon_type is not None else source.icon_type,
                icon_background=command.icon_background
                if command.icon_background is not None
                else source.icon_background,
                use_icon_as_answer_icon=int(source.use_icon_as_answer_icon),
                created_at=now,
                updated_at=now,
                maintainer_id=command.actor.id,
                created_by_id=command.actor.id,
                updated_by_id=command.actor.id,
            )
        )
        draft = await self.db.get(WorkflowDraft, command.source_app_id)
        if draft is not None:
            actor_json = json.dumps(asdict(command.actor), separators=(",", ":"))
            self.db.add(
                WorkflowDraft(
                    app_id=app_id,
                    workflow_id=str(uuid4()),
                    graph_json=draft.graph_json,
                    features_json=draft.features_json,
                    environment_variables_json=draft.environment_variables_json,
                    conversation_variables_json=draft.conversation_variables_json,
                    hash=draft.hash,
                    created_at=now,
                    updated_at=now,
                    created_by_json=actor_json,
                    updated_by_json=actor_json,
                )
            )
        await self.db.commit()
        return await self.get(principal, app_id)

    async def delete(self, principal: Principal, app_id: str) -> None:
        await self.access.require(principal, app_id, APP_DELETE)
        uploaded_files = (
            (await self.db.execute(select(UploadedFile).where(UploadedFile.app_id == app_id))).scalars().all()
        )
        app = await self.db.get(App, app_id)
        if app is None:
            raise AppNotFound
        app.published_workflow_id = None
        await self.db.flush()
        run_ids = select(WorkflowRun.id).where(WorkflowRun.app_id == app_id)
        await self.db.execute(delete(NodeExecution).where(NodeExecution.run_id.in_(run_ids)))
        await self.db.execute(delete(WorkflowRun).where(WorkflowRun.app_id == app_id))
        from api.models.workflow import WorkflowVersion

        await self.db.execute(delete(WorkflowVersion).where(WorkflowVersion.app_id == app_id))
        await self.db.execute(delete(WorkflowDraft).where(WorkflowDraft.app_id == app_id))
        await self.db.execute(delete(AppSite).where(AppSite.app_id == app_id))
        await self.db.execute(delete(WorkflowTool).where(WorkflowTool.app_id == app_id))
        await self.db.execute(delete(WorkflowTrigger).where(WorkflowTrigger.app_id == app_id))
        await self.db.execute(delete(UploadedFile).where(UploadedFile.app_id == app_id))
        await self.db.delete(app)
        await self.db.commit()
        await self.files.delete_content(uploaded_files)

    async def ensure_site(self, principal: Principal, app_id: str) -> AppSite:
        await self.access.require(principal, app_id, APP_PUBLISH)
        site = await self.db.get(AppSite, app_id)
        if site is None:
            now = int(time.time())
            site = AppSite(
                app_id=app_id,
                access_token=secrets.token_urlsafe(24),
                enable_site=1,
                created_at=now,
                updated_at=now,
            )
            self.db.add(site)
            await self.db.commit()
        return site

    async def set_site_enabled(self, principal: Principal, app_id: str, enabled: bool) -> bool | None:
        await self.access.require(principal, app_id, APP_PUBLISH)
        site = await self.db.get(AppSite, app_id)
        if site is None:
            return None
        site.enable_site = int(enabled)
        site.updated_at = int(time.time())
        await self.db.commit()
        return bool(site.enable_site)

    async def reset_site_access_token(self, principal: Principal, app_id: str) -> str | None:
        await self.access.require(principal, app_id, APP_PUBLISH)
        site = await self.db.get(AppSite, app_id)
        if site is None:
            return None
        site.access_token = secrets.token_urlsafe(24)
        site.updated_at = int(time.time())
        await self.db.commit()
        return site.access_token

    def app_json(
        self,
        record: AppRecord,
        permission_keys,
        *,
        detail: bool = True,
        site: AppSite | None = None,
    ) -> dict[str, Any]:
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
            "workflow": {"id": record.published_workflow_id} if record.published_workflow_id else None,
            "workflow_type": None,
            "workflow_kind": None,
        }
        if detail:
            result.update(
                {
                    "access_mode": None,
                    "api_base_url": app_config.PUBLIC_API_URL,
                    "bound_agent_id": None,
                    "deleted_tools": [],
                    "enable_site": bool(site.enable_site) if site is not None else False,
                    "enable_api": False,
                    "max_active_requests": None,
                    "model_config": None,
                    "site": self.site_json(site) if site is not None else None,
                    "tracing": None,
                }
            )
        return result

    @staticmethod
    def site_json(site: AppSite) -> dict[str, Any]:
        return {
            "access_token": site.access_token,
            "app_base_url": app_config.PUBLIC_WEB_URL.rstrip("/"),
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
