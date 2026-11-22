from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies.db import get_db
from api.dependencies.workflows import get_run_service
from api.models.app import App
from api.models.app import AppSite as AppSiteModel
from api.models.entities import Actor, AppId, AppRecord, AppSite, PublishedWorkflow
from api.responses import error_response
from api.routers.runs import stream_session
from api.schemas.public import PublicWorkflowRunPayload
from api.services.files import FileService
from api.services.run_service import RunNotFound, RunService
from api.services.workflows import DraftNotFound, InvalidWorkflow, WorkflowService

router = APIRouter()


class SiteUnavailable(Exception):
    pass


async def enabled_site(db: AsyncSession, access_token: str) -> AppSiteModel | None:
    return await db.scalar(
        select(AppSiteModel).where(
            AppSiteModel.access_token == access_token,
            AppSiteModel.enable_site == 1,
        )
    )


async def public_workflow(request: Request, db: AsyncSession) -> tuple[AppRecord, AppSite, PublishedWorkflow]:
    token = request.headers.get("X-App-Code", "")
    site_model = await enabled_site(db, token) if token else None
    if site_model is None:
        raise SiteUnavailable("Workflow site is unavailable")
    published = await WorkflowService(db).get_published(site_model.app_id)
    app_model = await db.get(App, site_model.app_id)
    if published is None or app_model is None:
        raise SiteUnavailable("Workflow site is unavailable")
    app = AppRecord(
        id=app_model.id,
        name=app_model.name,
        mode=app_model.mode,
        created_at=app_model.created_at,
        updated_at=app_model.updated_at if app_model.updated_at is not None else app_model.created_at,
        maintainer_id=app_model.maintainer_id,
        created_by_id=app_model.created_by_id,
        updated_by_id=app_model.updated_by_id,
        published_workflow_id=app_model.published_workflow_id,
        author_name=None,
        description=app_model.description or "",
        icon=app_model.icon,
        icon_type=app_model.icon_type,
        icon_background=app_model.icon_background,
        use_icon_as_answer_icon=bool(app_model.use_icon_as_answer_icon),
    )
    site = AppSite(
        app_id=AppId(site_model.app_id),
        access_token=site_model.access_token,
        enable_site=bool(site_model.enable_site),
        created_at=site_model.created_at,
        updated_at=site_model.updated_at,
    )
    return app, site, published


def start_variables(workflow: PublishedWorkflow) -> list[dict[str, Any]]:
    nodes = [node for node in workflow.graph.to_json_object()["nodes"] if node.get("data", {}).get("type") == "start"]
    if len(nodes) != 1:
        raise InvalidWorkflow("A published web workflow must contain exactly one user-input start node")
    variables = nodes[0]["data"].get("variables", [])
    if not isinstance(variables, list):
        raise InvalidWorkflow("Start node variables must be an array")
    return variables


def public_site_info(app: AppRecord) -> dict[str, Any]:
    return {
        "title": app.name,
        "icon_type": app.icon_type,
        "icon": app.icon,
        "icon_background": app.icon_background,
        "icon_url": None,
        "description": app.description,
        "default_language": "zh-CN",
        "show_workflow_steps": True,
        "use_icon_as_answer_icon": app.use_icon_as_answer_icon,
    }


@router.get("/site")
async def get_public_site(request: Request, db: AsyncSession = Depends(get_db)):
    app, _site, _published = await public_workflow(request, db)
    return {
        "app_id": app.id,
        "mode": "workflow",
        "can_replace_logo": False,
        "custom_config": None,
        "enable_site": True,
        "site": public_site_info(app),
    }


@router.get("/parameters")
async def get_public_parameters(request: Request, db: AsyncSession = Depends(get_db)):
    _app, _site, published = await public_workflow(request, db)
    try:
        variables = start_variables(published)
    except InvalidWorkflow as error:
        return error_response(400, "invalid_workflow", str(error))
    return {
        "user_input_form": [{variable["type"]: variable} for variable in variables],
        "more_like_this": None,
        "file_upload": {
            "enabled": False,
            "number_limits": 0,
            "allowed_file_upload_methods": [],
            "allowed_upload_methods": [],
        },
        "text_to_speech": None,
        "system_parameters": {},
    }


@router.get("/meta")
async def get_public_meta(request: Request, db: AsyncSession = Depends(get_db)):
    await public_workflow(request, db)
    return {"tool_icons": {}}


@router.get("/webapp/permission")
async def get_public_permission(request: Request, db: AsyncSession = Depends(get_db)):
    await public_workflow(request, db)
    return {"result": True}


@router.get("/login/status")
async def get_public_login_status(request: Request, app_code: str, db: AsyncSession = Depends(get_db)):
    if await enabled_site(db, app_code) is None:
        raise SiteUnavailable
    return {"logged_in": True, "app_logged_in": True}


@router.get("/webapp/access-mode")
async def get_public_access_mode(request: Request, appCode: str, db: AsyncSession = Depends(get_db)):
    if await enabled_site(db, appCode) is None:
        raise SiteUnavailable
    return {"accessMode": "public"}


@router.post("/workflows/run")
async def run_public_workflow(
    payload: PublicWorkflowRunPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    runs: RunService = Depends(get_run_service),
):
    app, _site, published = await public_workflow(request, db)
    if payload.files:
        return error_response(400, "files_not_supported", "File inputs are not supported")
    if payload.response_mode != "streaming":
        return error_response(400, "invalid_argument", "Only streaming response_mode is supported")
    try:
        start_variables(published)
        actor = Actor(id=f"webapp:{app.id}", name="Web App", email="")
        session = await runs.start_published(app.id, payload.inputs, actor)
    except (DraftNotFound, InvalidWorkflow) as error:
        return error_response(400, "invalid_workflow", str(error))
    return StreamingResponse(
        stream_session(session, actor, FileService(db)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/workflows/tasks/{task_id}/stop")
async def stop_public_workflow(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    runs: RunService = Depends(get_run_service),
):
    app, _site, _published = await public_workflow(request, db)
    try:
        await runs.stop(app.id, task_id)
    except RunNotFound:
        return error_response(404, "workflow_run_not_found", "Workflow run not found")
    return {"result": "success"}
