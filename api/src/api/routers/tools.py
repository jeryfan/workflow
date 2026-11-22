from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies.auth import require_principal
from api.dependencies.db import get_db
from api.dependencies.workflows import authorized_actor, get_run_service
from api.models.entities import WorkflowTool
from api.responses import error_response
from api.schemas.tools import (
    WorkflowToolCreatePayload,
    WorkflowToolDeletePayload,
    WorkflowToolPayload,
    WorkflowToolUpdatePayload,
)
from api.services.authorization import APP_PUBLISH, APP_READ, AccessDenied, AuthorizationService
from api.services.run_service import RunService
from api.services.tools import WorkflowToolConflict, WorkflowToolNotFound, WorkflowToolService

router = APIRouter()


async def require_tool_manager(request: Request, db: AsyncSession):
    principal = await require_principal(request, db)
    if "tool.manage" not in AuthorizationService(db).roles.account_keys(principal.role):
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


async def readable_tool(request: Request, db: AsyncSession, tool: WorkflowTool | None) -> WorkflowTool | None:
    if tool is None:
        return None
    await authorized_actor(request, tool.app_id, APP_READ, db)
    return tool


@router.get("/workspaces/current/tools/workflow")
async def list_workflow_tools(
    request: Request,
    db: AsyncSession = Depends(get_db),
    runs: RunService = Depends(get_run_service),
):
    await require_principal(request, db)
    tools = WorkflowToolService(db, runs)
    result = []
    for provider in await tools.list():
        try:
            await authorized_actor(request, provider["workflow_app_id"], APP_READ, db)
        except AccessDenied:
            continue
        result.append(provider)
    return result


@router.get("/workspaces/current/tool-provider/workflow/get")
async def get_workflow_tool(
    request: Request,
    workflow_app_id: str | None = None,
    workflow_tool_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    runs: RunService = Depends(get_run_service),
):
    tools = WorkflowToolService(db, runs)
    tool = (
        await tools.get_by_app(workflow_app_id)
        if workflow_app_id is not None
        else await tools.get(workflow_tool_id or "")
    )
    if await readable_tool(request, db, tool) is None:
        return error_response(404, "workflow_tool_not_found", "Workflow tool not found")
    return await tools.detail(tool)


@router.post("/workspaces/current/tool-provider/workflow/create")
async def create_workflow_tool(
    payload: WorkflowToolCreatePayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    runs: RunService = Depends(get_run_service),
):
    await require_tool_manager(request, db)
    await authorized_actor(request, payload.workflow_app_id, APP_PUBLISH, db)
    tools = WorkflowToolService(db, runs)
    try:
        tool = await tools.create(app_id=payload.workflow_app_id, **definition(payload))
    except WorkflowToolConflict as error:
        return error_response(409, "workflow_tool_conflict", str(error))
    except ValueError as error:
        return error_response(400, "invalid_argument", str(error))
    return await tools.detail(tool)


@router.post("/workspaces/current/tool-provider/workflow/update")
async def update_workflow_tool(
    payload: WorkflowToolUpdatePayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    runs: RunService = Depends(get_run_service),
):
    await require_tool_manager(request, db)
    tools = WorkflowToolService(db, runs)
    current = await tools.get(payload.workflow_tool_id)
    if current is None:
        return error_response(404, "workflow_tool_not_found", "Workflow tool not found")
    await authorized_actor(request, current.app_id, APP_PUBLISH, db)
    try:
        tool = await tools.update(payload.workflow_tool_id, **definition(payload))
    except WorkflowToolNotFound:
        return error_response(404, "workflow_tool_not_found", "Workflow tool not found")
    except WorkflowToolConflict as error:
        return error_response(409, "workflow_tool_conflict", str(error))
    except ValueError as error:
        return error_response(400, "invalid_argument", str(error))
    return await tools.detail(tool)


@router.post("/workspaces/current/tool-provider/workflow/delete")
async def delete_workflow_tool(
    payload: WorkflowToolDeletePayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    runs: RunService = Depends(get_run_service),
):
    await require_tool_manager(request, db)
    tool_id = payload.workflow_tool_id
    tools = WorkflowToolService(db, runs)
    current = await tools.get(tool_id)
    if current is None:
        return error_response(404, "workflow_tool_not_found", "Workflow tool not found")
    await authorized_actor(request, current.app_id, APP_PUBLISH, db)
    await tools.delete(tool_id)
    return {"result": "success"}
