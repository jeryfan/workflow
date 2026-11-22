from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Query, Request, Response

from api.config import PROJECT_ROOT
from api.dependencies.workflows import authorized_actor
from api.models.entities import (
    Draft,
    DraftConflict,
    PublishDraft,
    PublishedWorkflow,
)
from api.responses import error_response
from api.schemas.workflows import (
    EnvironmentVariableUpdatePayload,
    PublishWorkflowPayload,
    SyncDraftPayload,
    WorkflowUpdatePayload,
)
from api.services.authorization import (
    APP_EDIT,
    APP_PUBLISH,
    APP_READ,
)
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
def get_draft(app_id: str, request: Request):
    authorized_actor(request, app_id, APP_READ)
    service: WorkflowService = request.app.state.workflows
    try:
        return draft_response(service.get_draft(app_id))
    except DraftNotFound:
        return error_response(404, "draft_workflow_not_exist", "Draft workflow does not exist")


@router.post("/apps/{app_id}/workflows/draft")
def save_draft(app_id: str, payload: SyncDraftPayload, request: Request):
    actor = authorized_actor(request, app_id, APP_EDIT)
    service: WorkflowService = request.app.state.workflows
    if payload.is_collaborative:
        return error_response(400, "collaboration_not_supported", "Collaboration is not supported")
    try:
        saved = service.save_draft(service.parse_save_draft(app_id, payload, actor))
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
def get_published_workflow(app_id: str, request: Request) -> dict[str, Any] | None:
    authorized_actor(request, app_id, APP_READ)
    workflow = request.app.state.store.get_published(app_id)
    if workflow is None:
        return None
    result = published_response(workflow)
    result["tool_published"] = request.app.state.store.get_workflow_tool_by_app(app_id) is not None
    return result


@router.post("/apps/{app_id}/workflows/publish")
def publish_workflow(app_id: str, payload: PublishWorkflowPayload, request: Request) -> dict[str, Any]:
    actor = authorized_actor(request, app_id, APP_PUBLISH)
    draft = request.app.state.store.get_draft(app_id)
    if draft is not None:
        try:
            request.app.state.triggers.validate_graph(draft.graph)
        except ValueError as error:
            return error_response(400, "invalid_trigger", str(error))
    workflow = request.app.state.store.publish_draft(
        PublishDraft(
            app_id=app_id,
            actor=actor,
            marked_name=payload.marked_name or "",
            marked_comment=payload.marked_comment or "",
        )
    )
    if workflow is None:
        return error_response(404, "draft_workflow_not_exist", "Draft workflow does not exist")
    request.app.state.triggers.sync(workflow)
    request.app.state.store.ensure_site(app_id)
    return {"result": "success", "created_at": int(workflow.created_at.timestamp())}


@router.get("/apps/{app_id}/workflows")
def get_workflow_versions(
    app_id: str,
    request: Request,
    page: int = Query(default=1, ge=1, le=99999),
    limit: int = Query(default=10, ge=1, le=100),
    user_id: str | None = None,
    named_only: bool = False,
):
    actor = authorized_actor(request, app_id, APP_READ)
    user_id = user_id or None
    if user_id is not None and user_id != actor.id:
        return error_response(403, "permission_denied", "Cannot filter workflows by another user")
    workflows, has_more = request.app.state.store.list_workflow_versions(
        app_id,
        page,
        limit,
        user_id,
        named_only,
    )
    return {
        "items": [
            draft_response(workflow) if isinstance(workflow, Draft) else published_response(workflow)
            for workflow in workflows
        ],
        "page": page,
        "limit": limit,
        "has_more": has_more,
    }


@router.patch("/apps/{app_id}/workflows/{workflow_id}")
def update_workflow_version(app_id: str, workflow_id: str, payload: WorkflowUpdatePayload, request: Request):
    authorized_actor(request, app_id, APP_PUBLISH)
    if payload.marked_name is None and payload.marked_comment is None:
        return error_response(400, "invalid_argument", "No valid fields to update")
    workflow = request.app.state.store.update_workflow_version(
        app_id,
        workflow_id,
        payload.marked_name,
        payload.marked_comment,
    )
    if workflow is None:
        return error_response(404, "not_found", "Workflow not found")
    return published_response(workflow)


@router.delete("/apps/{app_id}/workflows/{workflow_id}")
def delete_workflow_version(app_id: str, workflow_id: str, request: Request):
    authorized_actor(request, app_id, APP_PUBLISH)
    result = request.app.state.store.delete_workflow_version(app_id, workflow_id)
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
def restore_workflow_version(app_id: str, workflow_id: str, request: Request):
    actor = authorized_actor(request, app_id, APP_PUBLISH)
    draft = request.app.state.store.restore_workflow_version(app_id, workflow_id, actor)
    if draft is None:
        return error_response(404, "not_found", "Workflow not found.")
    return {
        "result": "success",
        "hash": draft.hash,
        "updated_at": int(draft.updated_at.timestamp()),
    }


@router.get("/apps/{app_id}/workflows/draft/environment-variables")
def get_environment_variables(app_id: str, request: Request) -> dict[str, Any]:
    authorized_actor(request, app_id, APP_READ)
    service: WorkflowService = request.app.state.workflows
    try:
        draft = service.get_draft(app_id)
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
def update_environment_variables(
    app_id: str,
    payload: EnvironmentVariableUpdatePayload,
    request: Request,
):
    actor = authorized_actor(request, app_id, APP_EDIT)
    service: WorkflowService = request.app.state.workflows
    try:
        service.update_environment_variables(
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
def get_conversation_variables(app_id: str, request: Request) -> dict[str, Any]:
    authorized_actor(request, app_id, APP_READ)
    service: WorkflowService = request.app.state.workflows
    try:
        draft = service.get_draft(app_id)
    except DraftNotFound:
        return {"items": []}
    return {"items": list(draft.conversation_variables)}


@router.get("/apps/{app_id}/workflows/draft/variables")
def get_inspect_variables(app_id: str, request: Request) -> dict[str, Any]:
    authorized_actor(request, app_id, APP_READ)
    return {"items": [], "total": 0}


@router.get("/apps/{app_id}/workflows/draft/system-variables")
def get_system_variables(app_id: str, request: Request) -> dict[str, list]:
    authorized_actor(request, app_id, APP_READ)
    return {"items": []}


@router.get("/apps/{app_id}/workflows/default-workflow-block-configs")
def get_default_configs(app_id: str, request: Request) -> list[dict[str, Any]]:
    authorized_actor(request, app_id, APP_READ)
    service: WorkflowService = request.app.state.workflows
    return service.default_configs()


@router.get("/apps/{app_id}/workflows/default-workflow-block-configs/{block_type}")
def get_default_config(app_id: str, block_type: str, request: Request, q: str | None = None):
    authorized_actor(request, app_id, APP_READ)
    service: WorkflowService = request.app.state.workflows
    try:
        return service.default_config(block_type, q)
    except InvalidWorkflow:
        return {}
    except ValueError as error:
        return error_response(400, "invalid_argument", str(error))
