from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from api.dependencies.workflows import authorized_actor
from api.models.entities import (
    Actor,
    ContainerEvent,
    ExecutionEvent,
    NodeExecutionRecord,
    NodeFinished,
    NodeStarted,
    RunFailed,
    RunPartiallySucceeded,
    RunRecord,
    RunStopped,
    RunSucceeded,
    WorkflowFinished,
    WorkflowStarted,
)
from api.responses import error_response
from api.schemas.workflows import DraftRunPayload
from api.services.authorization import APP_READ, APP_RUN
from api.services.workflows import DraftNotFound, InvalidWorkflow, RunNotFound, RunService, RunSession, WorkflowService

router = APIRouter()


def execution_event_payload(event: ExecutionEvent, actor: Actor) -> dict[str, Any]:
    identity = event.identity
    base = {
        "task_id": identity.task_id,
        "workflow_run_id": identity.workflow_run_id,
    }
    if isinstance(event, WorkflowStarted):
        return {
            **base,
            "event": "workflow_started",
            "data": {
                "id": identity.workflow_run_id,
                "workflow_id": identity.workflow_id,
                "inputs": dict(event.inputs),
                "created_at": int(event.created_at.timestamp()),
                "reason": "initial",
            },
        }
    if isinstance(event, NodeStarted):
        return {
            **base,
            "event": "node_started",
            "data": {
                "id": event.execution_id,
                "node_id": event.node_id,
                "node_type": event.node_type,
                "title": event.title,
                "index": event.index,
                "predecessor_node_id": event.predecessor_node_id,
                "inputs": None,
                "inputs_truncated": False,
                "created_at": int(event.created_at.timestamp()),
                "extras": {},
                "iteration_id": None,
                "loop_id": None,
            },
        }
    if isinstance(event, NodeFinished):
        return {
            **base,
            "event": "node_finished",
            "data": {
                "id": event.execution_id,
                "node_id": event.node_id,
                "node_type": event.node_type,
                "title": event.title,
                "index": event.index,
                "predecessor_node_id": event.predecessor_node_id,
                "inputs": dict(event.inputs),
                "inputs_truncated": False,
                "process_data": dict(event.process_data),
                "process_data_truncated": False,
                "outputs": dict(event.outputs),
                "outputs_truncated": False,
                "status": event.status,
                "error": event.error,
                "elapsed_time": event.elapsed_time,
                "execution_metadata": {},
                "created_at": int(event.created_at.timestamp()),
                "finished_at": int(event.finished_at.timestamp()),
                "files": [],
                "iteration_id": None,
                "loop_id": None,
            },
        }
    if isinstance(event, ContainerEvent):
        return {**base, "event": event.event, "data": dict(event.data)}
    if isinstance(event, WorkflowFinished):
        status, outputs, error = workflow_outcome(event)
        return {
            **base,
            "event": "workflow_finished",
            "data": {
                "id": identity.workflow_run_id,
                "workflow_id": identity.workflow_id,
                "status": status,
                "outputs": outputs,
                "error": error,
                "elapsed_time": event.elapsed_time,
                "total_tokens": event.total_tokens,
                "total_steps": event.total_steps,
                "created_by": asdict(actor),
                "created_at": int(event.created_at.timestamp()),
                "finished_at": int(event.finished_at.timestamp()),
                "exceptions_count": event.outcome.exceptions_count if not isinstance(event.outcome, RunStopped) else 0,
                "files": [],
            },
        }
    raise TypeError(f"Unknown execution event: {type(event).__name__}")


def workflow_outcome(event: WorkflowFinished) -> tuple[str, dict[str, Any], str | None]:
    outcome = event.outcome
    if isinstance(outcome, RunSucceeded):
        return "succeeded", dict(outcome.outputs), None
    if isinstance(outcome, RunPartiallySucceeded):
        return "partial-succeeded", dict(outcome.outputs), None
    if isinstance(outcome, RunFailed):
        return "failed", dict(outcome.outputs), outcome.error
    if isinstance(outcome, RunStopped):
        return "stopped", dict(outcome.outputs), outcome.reason
    raise TypeError(f"Unknown run outcome: {type(outcome).__name__}")


def encode_sse(event: ExecutionEvent, actor: Actor, files=None) -> bytes:
    event_payload = execution_event_payload(event, actor)
    if files is not None and isinstance(event_payload.get("data"), dict):
        data = event_payload["data"]
        for key in ("inputs", "outputs", "process_data"):
            if key in data:
                data[key] = files.add_urls_to_value(data[key])
    payload = json.dumps(event_payload, ensure_ascii=False, separators=(",", ":"))
    return f"data: {payload}\n\n".encode()


def stream_session(session: RunSession, actor: Actor, files=None) -> Iterator[bytes]:
    try:
        for event in session.events():
            yield encode_sse(event, actor, files)
    finally:
        session.close()


def _timestamp(value) -> int | None:
    return int(value.timestamp()) if value is not None else None


def run_list_item(record: RunRecord) -> dict[str, Any]:
    return {
        "id": record.identity.workflow_run_id,
        "status": record.status,
        "created_at": _timestamp(record.created_at),
        "created_by_account": asdict(record.actor),
        "elapsed_time": record.elapsed_time,
        "exceptions_count": record.exceptions_count,
        "finished_at": _timestamp(record.finished_at),
        "retry_index": 0,
        "total_steps": record.total_steps,
        "total_tokens": record.total_tokens,
        "version": record.version,
    }


def run_detail(record: RunRecord, files=None) -> dict[str, Any]:
    inputs = dict(record.inputs)
    outputs = dict(record.outputs)
    if files is not None:
        inputs = files.add_urls_to_value(inputs)
        outputs = files.add_urls_to_value(outputs)
    return {
        **run_list_item(record),
        "created_by_end_user": None,
        "created_by_role": "account",
        "error": record.error,
        "graph": record.graph.to_json_object(),
        "inputs": inputs,
        "outputs": outputs,
    }


def node_execution_detail(record: NodeExecutionRecord, files=None) -> dict[str, Any]:
    inputs = dict(record.inputs)
    process_data = dict(record.process_data)
    outputs = dict(record.outputs)
    if files is not None:
        inputs = files.add_urls_to_value(inputs)
        process_data = files.add_urls_to_value(process_data)
        outputs = files.add_urls_to_value(outputs)
    return {
        "id": record.id,
        "node_id": record.node_id,
        "node_type": record.node_type,
        "title": record.title,
        "index": record.index,
        "predecessor_node_id": record.predecessor_node_id,
        "inputs": inputs,
        "inputs_truncated": False,
        "process_data": process_data,
        "process_data_truncated": False,
        "outputs": outputs,
        "outputs_truncated": False,
        "status": record.status,
        "error": record.error,
        "elapsed_time": record.elapsed_time,
        "execution_metadata": {},
        "extras": {},
        "created_at": _timestamp(record.created_at),
        "created_by_account": None,
        "created_by_end_user": None,
        "created_by_role": None,
        "finished_at": _timestamp(record.finished_at),
        "retry_index": 0,
    }


@router.get("/apps/{app_id}/workflows/draft/nodes/{node_id}/last-run")
def get_draft_node_last_run(app_id: str, node_id: str, request: Request):
    authorized_actor(request, app_id, APP_READ)
    service: WorkflowService = request.app.state.workflows
    try:
        draft = service.get_draft(app_id)
    except DraftNotFound:
        return error_response(404, "not_found", "Workflow not found")
    record = request.app.state.store.get_latest_node_execution(app_id, draft.workflow_id, node_id)
    if record is None:
        return error_response(404, "not_found", "last run not found")
    return node_execution_detail(record, request.app.state.files)


@router.get("/apps/{app_id}/workflow-runs")
def get_run_history(
    app_id: str,
    request: Request,
    last_id: str | None = None,
    limit: int = 20,
    status: str | None = None,
):
    authorized_actor(request, app_id, APP_READ)
    if not 1 <= limit <= 100:
        return error_response(400, "invalid_argument", "limit must be between 1 and 100")
    page = request.app.state.store.list_runs(app_id, limit, last_id, status)
    return {
        "data": [run_list_item(record) for record in page.records],
        "has_more": page.has_more,
        "limit": page.limit,
    }


@router.get("/apps/{app_id}/workflow-runs/{run_id}")
def get_run_detail(app_id: str, run_id: str, request: Request):
    authorized_actor(request, app_id, APP_READ)
    record = request.app.state.store.get_run_record(app_id, run_id)
    if record is None:
        return error_response(404, "workflow_run_not_found", "Workflow run not found")
    return run_detail(record, request.app.state.files)


@router.get("/apps/{app_id}/workflow-runs/{run_id}/node-executions")
def get_run_node_executions(app_id: str, run_id: str, request: Request):
    authorized_actor(request, app_id, APP_READ)
    records = request.app.state.store.list_node_executions(app_id, run_id)
    return {"data": [node_execution_detail(record, request.app.state.files) for record in records]}


@router.post("/apps/{app_id}/workflows/draft/run")
def run_draft(app_id: str, payload: DraftRunPayload, request: Request):
    actor = authorized_actor(request, app_id, APP_RUN)
    if payload.files:
        return error_response(400, "files_not_supported", "File inputs are not supported")
    service: RunService = request.app.state.runs
    try:
        session = service.start(app_id, payload.inputs, actor)
    except DraftNotFound:
        return error_response(404, "draft_workflow_not_exist", "Draft workflow does not exist")
    except InvalidWorkflow as error:
        return error_response(400, "invalid_argument", str(error))
    return StreamingResponse(
        stream_session(session, actor, request.app.state.files),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/apps/{app_id}/workflow-runs/tasks/{task_id}/stop")
def stop_run(app_id: str, task_id: str, request: Request):
    authorized_actor(request, app_id, APP_RUN)
    service: RunService = request.app.state.runs
    try:
        service.stop(app_id, task_id)
    except RunNotFound:
        return error_response(404, "workflow_run_not_found", "Workflow run does not exist")
    return {"result": "success"}
