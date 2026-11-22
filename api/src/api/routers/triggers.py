from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies.db import get_db
from api.dependencies.workflows import authorized_actor, get_run_service
from api.responses import error_response
from api.schemas.triggers import TriggerEnablePayload
from api.services.authorization import APP_EDIT, APP_READ
from api.services.run_service import RunService
from api.services.triggers import TriggerService, WebhookNotFound, WebhookRequestError

management_router = APIRouter()
public_router = APIRouter()


@management_router.get("/apps/{app_id}/triggers")
async def list_triggers(
    app_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    runs: RunService = Depends(get_run_service),
):
    await authorized_actor(request, app_id, APP_READ, db)
    return {"data": await TriggerService(db, runs).list(app_id)}


@management_router.post("/apps/{app_id}/trigger-enable")
async def set_trigger_enabled(
    app_id: str,
    payload: TriggerEnablePayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    runs: RunService = Depends(get_run_service),
):
    await authorized_actor(request, app_id, APP_EDIT, db)
    trigger = await TriggerService(db, runs).set_enabled(app_id, payload.trigger_id, payload.enable_trigger)
    if trigger is None:
        return error_response(404, "trigger_not_found", "Workflow trigger not found")
    return trigger


@management_router.get("/apps/{app_id}/workflows/triggers/webhook")
async def get_webhook(
    app_id: str,
    request: Request,
    node_id: str,
    db: AsyncSession = Depends(get_db),
    runs: RunService = Depends(get_run_service),
):
    actor = await authorized_actor(request, app_id, APP_READ, db)
    try:
        return await TriggerService(db, runs).webhook(app_id, node_id, actor)
    except WebhookNotFound:
        return error_response(404, "webhook_not_found", "Webhook trigger not found")


async def execute_webhook(
    request: Request,
    webhook_id: str,
    debug: bool,
    db: AsyncSession,
    runs: RunService,
):
    try:
        return await TriggerService(db, runs).execute_webhook(request, webhook_id, debug=debug)
    except WebhookNotFound:
        return error_response(404, "webhook_not_found", "Webhook trigger is unavailable")
    except WebhookRequestError as error:
        return error_response(400, "invalid_webhook_request", str(error))


@public_router.api_route(
    "/triggers/webhook/{webhook_id}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
    include_in_schema=False,
)
async def handle_webhook(
    webhook_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    runs: RunService = Depends(get_run_service),
):
    return await execute_webhook(request, webhook_id, False, db, runs)


@public_router.api_route(
    "/triggers/webhook-debug/{webhook_id}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
    include_in_schema=False,
)
async def handle_webhook_debug(
    webhook_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    runs: RunService = Depends(get_run_service),
):
    return await execute_webhook(request, webhook_id, True, db, runs)
