from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies.auth import require_principal
from api.dependencies.db import get_db
from api.dependencies.workflows import authorized_actor, get_run_service
from api.models.entities import (
    RunFailed,
    RunPartiallySucceeded,
    RunStopped,
    RunSucceeded,
    WorkflowFinished,
    WorkflowStarted,
)
from api.models.execution import WorkflowRun
from api.models.human_input import HumanInputForm
from api.responses import error_response
from api.schemas.auth import Principal
from api.schemas.human_input import HumanInputSubmitPayload
from api.services.authorization import APP_READ
from api.services.files import FileError, FileService
from api.services.human_input import HumanInputConflict, HumanInputError, HumanInputNotFound, HumanInputService
from api.services.run_service import RunConflict, RunNotFound, RunService
from api.services.runs import RunHistoryService

from .runs import encode_sse, stream_session

console_router = APIRouter()
public_router = APIRouter()


def human_input_error(error: HumanInputError):
    if isinstance(error, HumanInputNotFound):
        return error_response(404, "human_input_not_found", str(error))
    if isinstance(error, HumanInputConflict):
        return error_response(409, "human_input_conflict", str(error))
    return error_response(400, "invalid_human_input", str(error))


@console_router.get("/form/human_input/{token}")
async def console_form(
    token: str,
    _principal: Principal = Depends(require_principal),
    db: AsyncSession = Depends(get_db),
):
    # Kept behind both the capability token and an authenticated console session.
    try:
        service = HumanInputService(db)
        return service.response(await service.by_token(token))
    except HumanInputError as error:
        return human_input_error(error)


@public_router.get("/form/human_input/{token}")
async def public_form(token: str, db: AsyncSession = Depends(get_db)):
    try:
        return HumanInputService(db).response(await HumanInputService(db).by_token(token))
    except HumanInputError as error:
        return human_input_error(error)


@public_router.post("/form/human_input/{token}/upload-token")
async def public_upload_token(token: str, db: AsyncSession = Depends(get_db)):
    try:
        service = HumanInputService(db)
        return service.upload_token(await service.by_token(token))
    except HumanInputError as error:
        return human_input_error(error)


@console_router.post("/form/human_input/{token}")
async def submit_console_form(
    token: str,
    payload: HumanInputSubmitPayload,
    principal: Principal = Depends(require_principal),
    db: AsyncSession = Depends(get_db),
):
    try:
        await HumanInputService(db).submit(token, payload.inputs, payload.action, principal.account_id)
        return {"result": "success"}
    except HumanInputError as error:
        return human_input_error(error)


@public_router.post("/form/human_input/{token}")
async def submit_public_form(
    token: str,
    payload: HumanInputSubmitPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    runs: RunService = Depends(get_run_service),
):
    service = HumanInputService(db)
    try:
        form = await service.submit(token, payload.inputs, payload.action, "webapp")
        session = await runs.resume(form.workflow_run_id)
        session.close()
        return {"result": "success"}
    except HumanInputError as error:
        return human_input_error(error)
    except (RunNotFound, RunConflict):
        return error_response(409, "workflow_resume_conflict", "Workflow run cannot be resumed")


@public_router.post("/human-input-forms/files", status_code=201)
async def upload_human_input_file(request: Request, db: AsyncSession = Depends(get_db)):
    service = HumanInputService(db)
    try:
        authorization = request.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HumanInputError("Upload token is required")
        form = await service.verify_upload_token(token)
        payload = await request.form()
        files = FileService(db)
        remote_url = payload.get("url")
        if isinstance(remote_url, str) and remote_url:
            file = await files.fetch_remote(
                app_id=form.app_id,
                actor_id=form.created_by_id,
                url=remote_url,
            )
        else:
            upload = payload.get("file")
            if upload is None or isinstance(upload, str):
                raise HumanInputError("A file is required")
            content = await upload.read(20 * 1024 * 1024 + 1)
            file = await files.save(
                app_id=form.app_id,
                actor_id=form.created_by_id,
                filename=upload.filename or "",
                content=content,
                mime_type=upload.content_type,
            )
        await service.validate_upload(form, file)
        return files.response(file)
    except HumanInputError as error:
        return human_input_error(error)
    except FileError as error:
        return error_response(400, "invalid_file", str(error))


@console_router.get("/workflow/{run_id}/pause-details")
async def pause_details(run_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    run = await db.get(WorkflowRun, run_id)
    if run is None:
        return error_response(404, "workflow_run_not_found", "Workflow run not found")
    await authorized_actor(request, run.app_id, APP_READ, db)
    forms = (await db.execute(select(HumanInputForm).where(HumanInputForm.workflow_run_id == run_id))).scalars().all()
    return {
        "paused_nodes": [
            {
                "node_id": form.node_id,
                "pause_type": {"type": "human_input", "form_version": "1"},
            }
            for form in forms
            if form.status == "waiting"
        ]
    }


async def snapshot_events(
    runs: RunService, history: RunHistoryService, app_id: str, run_id: str, actor
) -> AsyncIterator[bytes]:
    record = await history.get(app_id, run_id)
    if record is None:
        return
    started = WorkflowStarted(
        identity=record.identity,
        sequence=0,
        inputs=record.inputs,
        created_at=record.created_at,
    )
    yield await encode_sse(started, actor)
    while record.status in {"running", "paused"}:
        if record.status == "paused":
            session = await runs.resume(run_id)
            async for chunk in stream_session(session, actor):
                yield chunk
            return
        await asyncio.sleep(0.1)
        record = await history.get(app_id, run_id)
        if record is None:
            return
    now = datetime.now(UTC)
    if record.status == "succeeded":
        outcome = RunSucceeded(outputs=record.outputs, exceptions_count=record.exceptions_count)
    elif record.status == "partial-succeeded":
        outcome = RunPartiallySucceeded(outputs=record.outputs, exceptions_count=record.exceptions_count)
    elif record.status == "stopped":
        outcome = RunStopped(outputs=record.outputs, reason=record.error or "Stopped by user")
    else:
        outcome = RunFailed(
            outputs=record.outputs, error=record.error or "Workflow failed", exceptions_count=record.exceptions_count
        )
    finished = WorkflowFinished(
        identity=record.identity,
        sequence=record.total_steps,
        outcome=outcome,
        elapsed_time=record.elapsed_time,
        total_tokens=record.total_tokens,
        total_steps=record.total_steps,
        created_at=record.created_at,
        finished_at=record.finished_at or now,
    )
    yield await encode_sse(finished, actor)


@console_router.get("/workflow/{run_id}/events")
async def workflow_events(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    runs: RunService = Depends(get_run_service),
):
    run = await db.get(WorkflowRun, run_id)
    if run is None:
        return error_response(404, "workflow_run_not_found", "Workflow run not found")
    actor = await authorized_actor(request, run.app_id, APP_READ, db)
    return StreamingResponse(
        snapshot_events(runs, RunHistoryService(db), run.app_id, run_id, actor),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
