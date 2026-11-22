from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from api.models.entities import Actor, AppRecord, AppSite, PublishedWorkflow
from api.responses import error_response
from api.routes.runs import stream_session
from api.schemas.public import PublicWorkflowRunPayload
from api.services.workflows import DraftNotFound, InvalidWorkflow, RunNotFound, RunService

router = APIRouter()


class SiteUnavailable(Exception):
    pass


def public_workflow(request: Request) -> tuple[AppRecord, AppSite, PublishedWorkflow]:
    token = request.headers.get("X-App-Code", "")
    site = request.app.state.store.get_enabled_site(token) if token else None
    if site is None:
        raise SiteUnavailable("Workflow site is unavailable")
    published = request.app.state.store.get_published(site.app_id)
    app = request.app.state.store.get_app_record(site.app_id)
    if published is None or app is None:
        raise SiteUnavailable("Workflow site is unavailable")
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
def get_public_site(request: Request):
    app, _site, _published = public_workflow(request)
    return {
        "app_id": app.id,
        "mode": "workflow",
        "can_replace_logo": False,
        "custom_config": None,
        "enable_site": True,
        "site": public_site_info(app),
    }


@router.get("/parameters")
def get_public_parameters(request: Request):
    _app, _site, published = public_workflow(request)
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
def get_public_meta(request: Request):
    public_workflow(request)
    return {"tool_icons": {}}


@router.get("/webapp/permission")
def get_public_permission(request: Request):
    public_workflow(request)
    return {"result": True}


@router.get("/login/status")
def get_public_login_status(request: Request, app_code: str):
    if request.app.state.store.get_enabled_site(app_code) is None:
        raise SiteUnavailable
    return {"logged_in": True, "app_logged_in": True}


@router.get("/webapp/access-mode")
def get_public_access_mode(request: Request, appCode: str):
    if request.app.state.store.get_enabled_site(appCode) is None:
        raise SiteUnavailable
    return {"accessMode": "public"}


@router.post("/workflows/run")
def run_public_workflow(payload: PublicWorkflowRunPayload, request: Request):
    app, _site, published = public_workflow(request)
    if payload.files:
        return error_response(400, "files_not_supported", "File inputs are not supported")
    if payload.response_mode != "streaming":
        return error_response(400, "invalid_argument", "Only streaming response_mode is supported")
    try:
        start_variables(published)
        actor = Actor(id=f"webapp:{app.id}", name="Web App", email="")
        service: RunService = request.app.state.runs
        session = service.start_published(app.id, payload.inputs, actor)
    except (DraftNotFound, InvalidWorkflow) as error:
        return error_response(400, "invalid_workflow", str(error))
    return StreamingResponse(
        stream_session(session, actor, request.app.state.files),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/workflows/tasks/{task_id}/stop")
def stop_public_workflow(task_id: str, request: Request):
    app, _site, _published = public_workflow(request)
    try:
        request.app.state.runs.stop(app.id, task_id)
    except RunNotFound:
        return error_response(404, "workflow_run_not_found", "Workflow run not found")
    return {"result": "success"}
