from __future__ import annotations

from fastapi import APIRouter, Request

from api.dependencies.workflows import authorized_actor
from api.responses import error_response
from api.schemas.triggers import TriggerEnablePayload
from api.services.authorization import APP_EDIT, APP_READ
from api.services.triggers import WebhookNotFound, WebhookRequestError

management_router = APIRouter()
public_router = APIRouter()


@management_router.get("/apps/{app_id}/triggers")
def list_triggers(app_id: str, request: Request):
    authorized_actor(request, app_id, APP_READ)
    return {"data": request.app.state.triggers.list(app_id)}


@management_router.post("/apps/{app_id}/trigger-enable")
def set_trigger_enabled(app_id: str, payload: TriggerEnablePayload, request: Request):
    authorized_actor(request, app_id, APP_EDIT)
    trigger = request.app.state.triggers.set_enabled(app_id, payload.trigger_id, payload.enable_trigger)
    if trigger is None:
        return error_response(404, "trigger_not_found", "Workflow trigger not found")
    return trigger


@management_router.get("/apps/{app_id}/workflows/triggers/webhook")
def get_webhook(app_id: str, request: Request, node_id: str):
    actor = authorized_actor(request, app_id, APP_READ)
    try:
        return request.app.state.triggers.webhook(app_id, node_id, actor)
    except WebhookNotFound:
        return error_response(404, "webhook_not_found", "Webhook trigger not found")


async def execute_webhook(request: Request, webhook_id: str, debug: bool):
    try:
        return await request.app.state.triggers.execute_webhook(request, webhook_id, debug=debug)
    except WebhookNotFound:
        return error_response(404, "webhook_not_found", "Webhook trigger is unavailable")
    except WebhookRequestError as error:
        return error_response(400, "invalid_webhook_request", str(error))


@public_router.api_route(
    "/triggers/webhook/{webhook_id}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
)
async def handle_webhook(webhook_id: str, request: Request):
    return await execute_webhook(request, webhook_id, False)


@public_router.api_route(
    "/triggers/webhook-debug/{webhook_id}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
)
async def handle_webhook_debug(webhook_id: str, request: Request):
    return await execute_webhook(request, webhook_id, True)
