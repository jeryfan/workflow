from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from api.configs import PROJECT_ROOT
from api.dependencies.auth import require_principal
from api.dependencies.db import get_db
from api.dependencies.workflows import authorized_actor, get_app_service, get_run_service, get_workflow_service
from api.models.entities import (
    Draft,
    DraftConflict,
    PublishDraft,
    PublishedWorkflow,
)
from api.responses import error_response
from api.schemas.auth import Principal
from api.schemas.workflows import (
    EnvironmentVariableUpdatePayload,
    PublishWorkflowPayload,
    SyncDraftPayload,
    WorkflowUpdatePayload,
)
from api.services.apps import AppService
from api.services.authorization import (
    APP_EDIT,
    APP_PUBLISH,
    APP_READ,
)
from api.services.run_service import RunService
from api.services.tools import WorkflowToolService
from api.services.triggers import TriggerService
from api.services.workflows import (
    DraftNotFound,
    InvalidWorkflow,
    WorkflowService,
)

router = APIRouter()
FRONTEND_CONTRACT_PATH = PROJECT_ROOT / "contracts" / "frontend-runtime.json"


def draft_response(draft: Draft) -> dict[str, Any]:
    return {
        "id": draft.workflow_id,
        "graph": draft.graph.to_json_object(),
        "features": dict(draft.features),
        "hash": draft.hash,
        "version": "draft",
        "version_number": None,
        "marked_name": "",
        "marked_comment": "",
        "created_by": asdict(draft.created_by),
        "created_at": int(draft.created_at.timestamp()),
        "updated_by": asdict(draft.updated_by),
        "updated_at": int(draft.updated_at.timestamp()),
        "tool_published": False,
        "environment_variables": list(draft.environment_variables),
        "conversation_variables": list(draft.conversation_variables),
        "rag_pipeline_variables": [],
    }


@router.get("/apps/{app_id}/workflows/draft")
async def get_draft(
    app_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    workflows: WorkflowService = Depends(get_workflow_service),
):
    await authorized_actor(request, app_id, APP_READ, db)
    try:
        return draft_response(await workflows.get_draft(app_id))
    except DraftNotFound:
        return error_response(404, "draft_workflow_not_exist", "Draft workflow does not exist")


@router.post("/apps/{app_id}/workflows/draft")
async def save_draft(
    app_id: str,
    payload: SyncDraftPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    workflows: WorkflowService = Depends(get_workflow_service),
):
    actor = await authorized_actor(request, app_id, APP_EDIT, db)
    if payload.is_collaborative:
        return error_response(400, "collaboration_not_supported", "Collaboration is not supported")
    try:
        saved = await workflows.save_draft(workflows.parse_save_draft(app_id, payload, actor))
    except DraftConflict:
        return error_response(409, "draft_workflow_not_sync", "Draft workflow has changed")
    except (InvalidWorkflow, ValueError) as error:
        return error_response(400, "invalid_argument", str(error))
    return {
        "result": "success",
        "hash": saved.hash,
        "updated_at": int(saved.updated_at.timestamp()),
    }


def published_response(workflow: PublishedWorkflow) -> dict[str, Any]:
    return {
        "id": workflow.id,
        "graph": workflow.graph.to_json_object(),
        "features": dict(workflow.features),
        "hash": workflow.hash,
        "version": str(workflow.created_at.timestamp()),
        "version_number": workflow.version_number,
        "marked_name": workflow.marked_name,
        "marked_comment": workflow.marked_comment,
        "created_by": asdict(workflow.created_by),
        "created_at": int(workflow.created_at.timestamp()),
        "updated_by": asdict(workflow.created_by),
        "updated_at": int(workflow.created_at.timestamp()),
        "tool_published": False,
        "environment_variables": list(workflow.environment_variables),
        "conversation_variables": list(workflow.conversation_variables),
        "rag_pipeline_variables": [],
    }


@router.get("/apps/{app_id}/workflows/publish")
async def get_published_workflow(
    app_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    workflows: WorkflowService = Depends(get_workflow_service),
    runs: RunService = Depends(get_run_service),
) -> dict[str, Any] | None:
    await authorized_actor(request, app_id, APP_READ, db)
    workflow = await workflows.get_published(app_id)
    if workflow is None:
        return None
    result = published_response(workflow)
    result["tool_published"] = await WorkflowToolService(db, runs).get_by_app(app_id) is not None
    return result


@router.post("/apps/{app_id}/workflows/publish")
async def publish_workflow(
    app_id: str,
    payload: PublishWorkflowPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    workflows: WorkflowService = Depends(get_workflow_service),
    runs: RunService = Depends(get_run_service),
    principal: Principal = Depends(require_principal),
    apps: AppService = Depends(get_app_service),
) -> dict[str, Any]:
    actor = await authorized_actor(request, app_id, APP_PUBLISH, db)
    triggers = TriggerService(db, runs)
    try:
        draft = await workflows.get_draft(app_id)
    except DraftNotFound:
        draft = None
    if draft is not None:
        try:
            triggers.validate_graph(draft.graph)
        except ValueError as error:
            return error_response(400, "invalid_trigger", str(error))
    workflow = await workflows.publish_draft(
        PublishDraft(
            app_id=app_id,
            actor=actor,
            marked_name=payload.marked_name or "",
            marked_comment=payload.marked_comment or "",
        )
    )
    if workflow is None:
        return error_response(404, "draft_workflow_not_exist", "Draft workflow does not exist")
    await triggers.sync(workflow)
    await apps.ensure_site(principal, app_id)
    return {"result": "success", "created_at": int(workflow.created_at.timestamp())}


@router.get("/apps/{app_id}/workflows")
async def get_workflow_versions(
    app_id: str,
    request: Request,
    page: int = Query(default=1, ge=1, le=99999),
    limit: int = Query(default=10, ge=1, le=100),
    user_id: str | None = None,
    named_only: bool = False,
    db: AsyncSession = Depends(get_db),
    workflows: WorkflowService = Depends(get_workflow_service),
):
    actor = await authorized_actor(request, app_id, APP_READ, db)
    user_id = user_id or None
    if user_id is not None and user_id != actor.id:
        return error_response(403, "permission_denied", "Cannot filter workflows by another user")
    items, has_more = await workflows.list_workflow_versions(
        app_id,
        page,
        limit,
        user_id,
        named_only,
    )
    return {
        "items": [
            draft_response(workflow) if isinstance(workflow, Draft) else published_response(workflow)
            for workflow in items
        ],
        "page": page,
        "limit": limit,
        "has_more": has_more,
    }


@router.patch("/apps/{app_id}/workflows/{workflow_id}")
async def update_workflow_version(
    app_id: str,
    workflow_id: str,
    payload: WorkflowUpdatePayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    workflows: WorkflowService = Depends(get_workflow_service),
):
    await authorized_actor(request, app_id, APP_PUBLISH, db)
    if payload.marked_name is None and payload.marked_comment is None:
        return error_response(400, "invalid_argument", "No valid fields to update")
    workflow = await workflows.update_workflow_version(
        app_id,
        workflow_id,
        payload.marked_name,
        payload.marked_comment,
    )
    if workflow is None:
        return error_response(404, "not_found", "Workflow not found")
    return published_response(workflow)


@router.delete("/apps/{app_id}/workflows/{workflow_id}")
async def delete_workflow_version(
    app_id: str,
    workflow_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    workflows: WorkflowService = Depends(get_workflow_service),
):
    await authorized_actor(request, app_id, APP_PUBLISH, db)
    result = await workflows.delete_workflow_version(app_id, workflow_id)
    if result == "not_found":
        return error_response(404, "not_found", f"Workflow with ID {workflow_id} not found")
    if result == "current":
        return error_response(
            400,
            "invalid_argument",
            f"Cannot delete workflow that is currently in use by app '{app_id}'",
        )
    if result == "draft":
        return error_response(400, "invalid_argument", "Cannot delete draft workflow versions")
    return Response(status_code=204)


@router.post("/apps/{app_id}/workflows/{workflow_id}/restore")
async def restore_workflow_version(
    app_id: str,
    workflow_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    workflows: WorkflowService = Depends(get_workflow_service),
):
    actor = await authorized_actor(request, app_id, APP_PUBLISH, db)
    draft = await workflows.restore_workflow_version(app_id, workflow_id, actor)
    if draft is None:
        return error_response(404, "not_found", "Workflow not found.")
    return {
        "result": "success",
        "hash": draft.hash,
        "updated_at": int(draft.updated_at.timestamp()),
    }


@router.get("/apps/{app_id}/workflows/draft/environment-variables")
async def get_environment_variables(
    app_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    workflows: WorkflowService = Depends(get_workflow_service),
) -> dict[str, Any]:
    await authorized_actor(request, app_id, APP_READ, db)
    try:
        draft = await workflows.get_draft(app_id)
    except DraftNotFound:
        return {"items": []}
    return {
        "items": [
            {
                **dict(variable),
                "type": "env",
                "selector": ["env", variable["name"]],
                "edited": False,
                "visible": True,
                "editable": True,
            }
            for variable in draft.environment_variables
        ]
    }


@router.post("/apps/{app_id}/workflows/draft/environment-variables")
async def update_environment_variables(
    app_id: str,
    payload: EnvironmentVariableUpdatePayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    workflows: WorkflowService = Depends(get_workflow_service),
):
    actor = await authorized_actor(request, app_id, APP_EDIT, db)
    try:
        await workflows.update_environment_variables(
            app_id,
            [variable.model_dump() for variable in payload.environment_variables],
            payload.deleted_environment_variable_ids,
            payload.patch,
            actor,
        )
    except DraftNotFound:
        return error_response(404, "draft_workflow_not_exist", "Draft workflow does not exist")
    except ValueError as error:
        return error_response(400, "invalid_argument", str(error))
    return {"result": "success"}


@router.get("/apps/{app_id}/workflows/draft/conversation-variables")
async def get_conversation_variables(
    app_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    workflows: WorkflowService = Depends(get_workflow_service),
) -> dict[str, Any]:
    await authorized_actor(request, app_id, APP_READ, db)
    try:
        draft = await workflows.get_draft(app_id)
    except DraftNotFound:
        return {"items": []}
    return {"items": list(draft.conversation_variables)}


@router.get("/apps/{app_id}/workflows/draft/variables")
async def get_inspect_variables(
    app_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await authorized_actor(request, app_id, APP_READ, db)
    return {"items": [], "total": 0}


@router.get("/apps/{app_id}/workflows/draft/system-variables")
async def get_system_variables(
    app_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, list]:
    await authorized_actor(request, app_id, APP_READ, db)
    return {"items": []}


@router.get("/apps/{app_id}/workflows/default-workflow-block-configs")
async def get_default_configs(
    app_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    workflows: WorkflowService = Depends(get_workflow_service),
) -> list[dict[str, Any]]:
    await authorized_actor(request, app_id, APP_READ, db)
    return workflows.default_configs()


@router.get("/apps/{app_id}/workflows/default-workflow-block-configs/{block_type}")
async def get_default_config(
    app_id: str,
    block_type: str,
    request: Request,
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
    workflows: WorkflowService = Depends(get_workflow_service),
):
    await authorized_actor(request, app_id, APP_READ, db)
    try:
        return workflows.default_config(block_type, q)
    except InvalidWorkflow:
        return {}
    except ValueError as error:
        return error_response(400, "invalid_argument", str(error))
