from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from api.dependencies.auth import require_principal
from api.dependencies.workflows import authorized_actor
from api.models.entities import WorkflowTool
from api.responses import error_response
from api.schemas.tools import (
    WorkflowToolCreatePayload,
    WorkflowToolDeletePayload,
    WorkflowToolPayload,
    WorkflowToolUpdatePayload,
)
from api.services.authorization import APP_PUBLISH, APP_READ, AccessDenied
from api.services.tools import WorkflowToolConflict, WorkflowToolNotFound

router = APIRouter()


def require_tool_manager(request: Request):
    principal = require_principal(request)
    if "tool.manage" not in request.app.state.authorization.roles.account_keys(principal.role):
        raise AccessDenied("tool.manage")
    return principal


def definition(payload: WorkflowToolPayload) -> dict[str, Any]:
    return {
        "name": payload.name,
        "label": payload.label,
        "description": payload.description,
        "icon": payload.icon,
        "parameters": tuple(parameter.model_dump() for parameter in payload.parameters),
        "labels": tuple(payload.labels),
        "privacy_policy": payload.privacy_policy,
    }


def readable_tool(request: Request, tool: WorkflowTool | None) -> WorkflowTool | None:
    if tool is None:
        return None
    authorized_actor(request, tool.app_id, APP_READ)
    return tool


@router.get("/workspaces/current/tools/workflow")
def list_workflow_tools(request: Request):
    require_principal(request)
    result = []
    for provider in request.app.state.workflow_tools.list():
        try:
            authorized_actor(request, provider["workflow_app_id"], APP_READ)
        except AccessDenied:
            continue
        result.append(provider)
    return result


@router.get("/workspaces/current/tool-provider/workflow/get")
def get_workflow_tool(
    request: Request,
    workflow_app_id: str | None = None,
    workflow_tool_id: str | None = None,
):
    store = request.app.state.store
    tool = (
        store.get_workflow_tool_by_app(workflow_app_id)
        if workflow_app_id is not None
        else store.get_workflow_tool(workflow_tool_id or "")
    )
    if readable_tool(request, tool) is None:
        return error_response(404, "workflow_tool_not_found", "Workflow tool not found")
    return request.app.state.workflow_tools.detail(tool)


@router.post("/workspaces/current/tool-provider/workflow/create")
def create_workflow_tool(payload: WorkflowToolCreatePayload, request: Request):
    require_tool_manager(request)
    authorized_actor(request, payload.workflow_app_id, APP_PUBLISH)
    try:
        tool = request.app.state.workflow_tools.create(app_id=payload.workflow_app_id, **definition(payload))
    except WorkflowToolConflict as error:
        return error_response(409, "workflow_tool_conflict", str(error))
    except ValueError as error:
        return error_response(400, "invalid_argument", str(error))
    return request.app.state.workflow_tools.detail(tool)


@router.post("/workspaces/current/tool-provider/workflow/update")
def update_workflow_tool(payload: WorkflowToolUpdatePayload, request: Request):
    require_tool_manager(request)
    current = request.app.state.store.get_workflow_tool(payload.workflow_tool_id)
    if current is None:
        return error_response(404, "workflow_tool_not_found", "Workflow tool not found")
    authorized_actor(request, current.app_id, APP_PUBLISH)
    try:
        tool = request.app.state.workflow_tools.update(payload.workflow_tool_id, **definition(payload))
    except WorkflowToolNotFound:
        return error_response(404, "workflow_tool_not_found", "Workflow tool not found")
    except WorkflowToolConflict as error:
        return error_response(409, "workflow_tool_conflict", str(error))
    except ValueError as error:
        return error_response(400, "invalid_argument", str(error))
    return request.app.state.workflow_tools.detail(tool)


@router.post("/workspaces/current/tool-provider/workflow/delete")
def delete_workflow_tool(payload: WorkflowToolDeletePayload, request: Request):
    require_tool_manager(request)
    tool_id = payload.workflow_tool_id
    current = request.app.state.store.get_workflow_tool(tool_id)
    if current is None:
        return error_response(404, "workflow_tool_not_found", "Workflow tool not found")
    authorized_actor(request, current.app_id, APP_PUBLISH)
    request.app.state.store.delete_workflow_tool(tool_id)
    return {"result": "success"}
