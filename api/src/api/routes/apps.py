from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Query, Request, Response

from api.dependencies.auth import require_principal
from api.dependencies.workflows import authorized_actor
from api.models.entities import CopyAppCommand, CreateAppCommand, DraftConflict, UpdateAppCommand
from api.responses import error_response
from api.schemas.apps import AppImportPayload, CopyAppPayload, CreateAppPayload, SiteEnablePayload, UpdateAppPayload
from api.services.apps import AppService, InvalidApp
from api.services.authorization import APP_IMPORT_EXPORT, APP_PUBLISH, APP_READ, AccessDenied, AppNotFound
from api.services.dsl import DslService, WorkflowVersionNotFound
from api.services.workflows import DraftNotFound

router = APIRouter()


@router.get("/apps")
def list_apps(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=30, ge=1, le=100),
    name: str | None = None,
    mode: str | None = None,
    sort_by: str = "last_modified",
    creator_ids: list[str] | None = None,
    is_created_by_me: bool = False,
):
    principal = require_principal(request)
    service: AppService = request.app.state.apps
    return service.list(
        principal,
        page=page,
        limit=limit,
        name=name,
        mode=mode,
        sort_by=sort_by,
        creator_ids=creator_ids,
        is_created_by_me=is_created_by_me,
    )


@router.post("/apps", status_code=201)
def create_app(payload: CreateAppPayload, request: Request):
    principal = require_principal(request)
    service: AppService = request.app.state.apps
    try:
        return service.create(
            principal,
            CreateAppCommand(
                name=payload.name,
                mode=payload.mode,
                actor=principal.actor,
                description=payload.description or "",
                icon=payload.icon or "🤖",
                icon_type=payload.icon_type or "emoji",
                icon_background=payload.icon_background or "#FFEAD5",
            ),
        )
    except InvalidApp as error:
        return error_response(400, "invalid_argument", str(error))


@router.post("/apps/{app_id}/copy", status_code=201)
def copy_app(app_id: str, payload: CopyAppPayload, request: Request):
    principal = require_principal(request)
    service: AppService = request.app.state.apps
    return service.copy(
        principal,
        CopyAppCommand(
            source_app_id=app_id,
            actor=principal.actor,
            name=payload.name,
            description=payload.description,
            icon=payload.icon,
            icon_type=payload.icon_type,
            icon_background=payload.icon_background,
        ),
    )


@router.put("/apps/{app_id}")
def update_app(app_id: str, payload: UpdateAppPayload, request: Request):
    principal = require_principal(request)
    service: AppService = request.app.state.apps
    try:
        return service.update(
            principal,
            UpdateAppCommand(
                app_id=app_id,
                actor=principal.actor,
                name=payload.name,
                description=payload.description,
                icon=payload.icon,
                icon_type=payload.icon_type,
                icon_background=payload.icon_background,
                use_icon_as_answer_icon=payload.use_icon_as_answer_icon,
            ),
        )
    except InvalidApp as error:
        return error_response(400, "invalid_argument", str(error))


@router.delete("/apps/{app_id}", status_code=204)
def delete_app(app_id: str, request: Request):
    principal = require_principal(request)
    request.app.state.apps.delete(principal, app_id)
    return Response(status_code=204)


@router.get("/apps/{app_id}")
def get_app(app_id: str, request: Request) -> dict[str, Any]:
    principal = require_principal(request)
    service: AppService = request.app.state.apps
    try:
        return service.get(principal, app_id)
    except AccessDenied as error:
        raise AppNotFound from error


@router.post("/apps/{app_id}/site-enable")
def set_app_site_enabled(app_id: str, payload: SiteEnablePayload, request: Request):
    authorized_actor(request, app_id, APP_PUBLISH)
    site = request.app.state.store.set_site_enabled(app_id, payload.enable_site)
    if site is None:
        return error_response(404, "site_not_found", "Publish the workflow before enabling its site")
    return {"result": "success", "enable_site": site.enable_site}


@router.post("/apps/{app_id}/site/access-token/reset")
def reset_app_site_access_token(app_id: str, request: Request):
    authorized_actor(request, app_id, APP_PUBLISH)
    site = request.app.state.store.reset_site_access_token(app_id)
    if site is None:
        return error_response(404, "site_not_found", "Workflow site not found")
    return {"access_token": site.access_token}


@router.get("/apps/{app_id}/export")
def export_app_dsl(
    app_id: str,
    request: Request,
    include_secret: bool = False,
    workflow_id: str | None = None,
    version_id: str | None = None,
    format: str = "yaml",
):
    principal = require_principal(request)
    authorization = request.app.state.authorization.require(principal, app_id, APP_IMPORT_EXPORT)
    if version_id is not None:
        return error_response(400, "invalid_argument", "Agent version export is not supported")
    if format != "yaml":
        return error_response(400, "invalid_argument", "Only YAML DSL export is supported")
    service: DslService = request.app.state.dsl
    try:
        content = service.export(
            app_id,
            app_name=authorization.resource.name,
            include_secret=include_secret,
            workflow_id=workflow_id,
        )
    except DraftNotFound:
        return error_response(404, "draft_workflow_not_exist", "Draft workflow does not exist")
    except WorkflowVersionNotFound:
        return error_response(404, "not_found", "Workflow version not found")
    return {"data": content}


@router.post("/apps/imports")
def import_app_dsl(payload: AppImportPayload, request: Request):
    principal = require_principal(request)
    service: DslService = request.app.state.dsl
    if payload.mode != "yaml-content":
        return service.failed(str(uuid4()), "Only yaml-content import is supported", app_id=payload.app_id)
    app_id = payload.app_id
    if app_id is None:
        apps: AppService = request.app.state.apps
        created = apps.create(
            principal,
            CreateAppCommand(
                name=payload.name or "Workflow",
                mode="workflow",
                actor=principal.actor,
                description=payload.description or "",
                icon=payload.icon or "🤖",
                icon_type=payload.icon_type or "emoji",
                icon_background=payload.icon_background or "#FFEAD5",
            ),
        )
        app_id = created["id"]
        try:
            result = service.stage_or_import(
                app_id=app_id,
                content=payload.yaml_content or "",
                name=payload.name,
                principal=principal,
            )
        except Exception:
            apps.delete(principal, app_id)
            raise
        if result["status"] == "failed":
            apps.delete(principal, app_id)
        return result
    request.app.state.authorization.require(principal, app_id, APP_IMPORT_EXPORT)
    try:
        return service.stage_or_import(
            app_id=app_id,
            content=payload.yaml_content or "",
            name=payload.name,
            principal=principal,
        )
    except DraftConflict:
        return error_response(409, "draft_workflow_not_sync", "Draft workflow has changed")


@router.post("/apps/imports/{import_id}/confirm")
def confirm_app_dsl_import(import_id: str, request: Request):
    principal = require_principal(request)
    service: DslService = request.app.state.dsl
    pending = service.pending_import(import_id, principal)
    if pending is None:
        return service.failed(import_id, "Import information expired or does not exist")
    request.app.state.authorization.require(principal, pending.app_id, APP_IMPORT_EXPORT)
    try:
        return service.confirm(import_id, principal)
    except DraftConflict:
        return error_response(409, "draft_workflow_not_sync", "Draft workflow has changed")


@router.get("/apps/imports/{app_id}/check-dependencies")
def check_app_dsl_dependencies(app_id: str, request: Request) -> dict[str, list]:
    authorized_actor(request, app_id, APP_READ)
    return {"leaked_dependencies": []}
