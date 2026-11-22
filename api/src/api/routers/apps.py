from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Query

from api.dependencies.auth import require_principal
from api.dependencies.workflows import get_app_service, get_dsl_service
from api.models.entities import CopyAppCommand, CreateAppCommand, DraftConflict, UpdateAppCommand
from api.responses import error_response
from api.schemas.apps import AppImportPayload, CopyAppPayload, CreateAppPayload, SiteEnablePayload, UpdateAppPayload
from api.schemas.auth import Principal
from api.services.apps import AppService, InvalidApp
from api.services.authorization import APP_IMPORT_EXPORT, APP_READ, AccessDenied, AppNotFound
from api.services.dsl import DslService, WorkflowVersionNotFound
from api.services.workflows import DraftNotFound

router = APIRouter()


@router.get("/apps")
async def list_apps(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=30, ge=1, le=100),
    name: str | None = None,
    mode: str | None = None,
    sort_by: str = "last_modified",
    creator_ids: list[str] | None = Query(default=None),
    is_created_by_me: bool = False,
    principal: Principal = Depends(require_principal),
    apps: AppService = Depends(get_app_service),
):
    return await apps.list(
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
async def create_app(
    payload: CreateAppPayload,
    principal: Principal = Depends(require_principal),
    apps: AppService = Depends(get_app_service),
):
    try:
        return await apps.create(
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
async def copy_app(
    app_id: str,
    payload: CopyAppPayload,
    principal: Principal = Depends(require_principal),
    apps: AppService = Depends(get_app_service),
):
    return await apps.copy(
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
async def update_app(
    app_id: str,
    payload: UpdateAppPayload,
    principal: Principal = Depends(require_principal),
    apps: AppService = Depends(get_app_service),
):
    try:
        return await apps.update(
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
async def delete_app(
    app_id: str,
    principal: Principal = Depends(require_principal),
    apps: AppService = Depends(get_app_service),
):
    await apps.delete(principal, app_id)


@router.get("/apps/{app_id}")
async def get_app(
    app_id: str,
    principal: Principal = Depends(require_principal),
    apps: AppService = Depends(get_app_service),
) -> dict[str, Any]:
    try:
        return await apps.get(principal, app_id)
    except AccessDenied as error:
        raise AppNotFound from error


@router.post("/apps/{app_id}/site-enable")
async def set_app_site_enabled(
    app_id: str,
    payload: SiteEnablePayload,
    principal: Principal = Depends(require_principal),
    apps: AppService = Depends(get_app_service),
):
    enabled = await apps.set_site_enabled(principal, app_id, payload.enable_site)
    if enabled is None:
        return error_response(404, "site_not_found", "Publish the workflow before enabling its site")
    return {"result": "success", "enable_site": enabled}


@router.post("/apps/{app_id}/site/access-token/reset")
async def reset_app_site_access_token(
    app_id: str,
    principal: Principal = Depends(require_principal),
    apps: AppService = Depends(get_app_service),
):
    token = await apps.reset_site_access_token(principal, app_id)
    if token is None:
        return error_response(404, "site_not_found", "Workflow site not found")
    return {"access_token": token}


@router.get("/apps/{app_id}/export")
async def export_app_dsl(
    app_id: str,
    include_secret: bool = False,
    workflow_id: str | None = None,
    version_id: str | None = None,
    format: str = "yaml",
    principal: Principal = Depends(require_principal),
    apps: AppService = Depends(get_app_service),
    dsl: DslService = Depends(get_dsl_service),
):
    authorization = await apps.access.require(principal, app_id, APP_IMPORT_EXPORT)
    if version_id is not None:
        return error_response(400, "invalid_argument", "Agent version export is not supported")
    if format != "yaml":
        return error_response(400, "invalid_argument", "Only YAML DSL export is supported")
    try:
        content = await dsl.export(
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
async def import_app_dsl(
    payload: AppImportPayload,
    principal: Principal = Depends(require_principal),
    apps: AppService = Depends(get_app_service),
    dsl: DslService = Depends(get_dsl_service),
):
    if payload.mode != "yaml-content":
        return dsl.failed(str(uuid4()), "Only yaml-content import is supported", app_id=payload.app_id)
    app_id = payload.app_id
    if app_id is None:
        created = await apps.create(
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
            result = await dsl.stage_or_import(
                app_id=app_id,
                content=payload.yaml_content or "",
                name=payload.name,
                principal=principal,
            )
        except Exception:
            await apps.delete(principal, app_id)
            raise
        if result["status"] == "failed":
            await apps.delete(principal, app_id)
        return result
    await apps.access.require(principal, app_id, APP_IMPORT_EXPORT)
    try:
        return await dsl.stage_or_import(
            app_id=app_id,
            content=payload.yaml_content or "",
            name=payload.name,
            principal=principal,
        )
    except DraftConflict:
        return error_response(409, "draft_workflow_not_sync", "Draft workflow has changed")


@router.post("/apps/imports/{import_id}/confirm")
async def confirm_app_dsl_import(
    import_id: str,
    principal: Principal = Depends(require_principal),
    apps: AppService = Depends(get_app_service),
    dsl: DslService = Depends(get_dsl_service),
):
    pending = dsl.pending_import(import_id, principal)
    if pending is None:
        return dsl.failed(import_id, "Import information expired or does not exist")
    await apps.access.require(principal, pending.app_id, APP_IMPORT_EXPORT)
    try:
        return await dsl.confirm(import_id, principal)
    except DraftConflict:
        return error_response(409, "draft_workflow_not_sync", "Draft workflow has changed")


@router.get("/apps/imports/{app_id}/check-dependencies")
async def check_app_dsl_dependencies(
    app_id: str,
    principal: Principal = Depends(require_principal),
    apps: AppService = Depends(get_app_service),
) -> dict[str, list]:
    await apps.access.require(principal, app_id, APP_READ)
    return {"leaked_dependencies": []}
