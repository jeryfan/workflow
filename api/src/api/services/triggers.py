from __future__ import annotations

import json
import logging
import threading
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadCronError, CroniterBadDateError, croniter
from fastapi import Request
from fastapi.responses import JSONResponse

from api.db.store import Store
from api.models.entities import Actor, AppId, PublishedWorkflow, WorkflowTrigger
from api.services.workflows import RunService
from graphon.nodes.trigger_webhook.entities import WebhookData

logger = logging.getLogger(__name__)
TRIGGER_TYPES = frozenset({"trigger-schedule", "trigger-webhook"})


def convert_12h_to_24h(value: str) -> tuple[int, int]:
    try:
        time_part, period = value.strip().upper().split()
        hour_text, minute_text = time_part.split(":")
        hour = int(hour_text)
        minute = int(minute_text)
    except (ValueError, AttributeError) as error:
        raise ValueError(f"Invalid time format: {value!r}") from error
    if period not in {"AM", "PM"} or not 1 <= hour <= 12 or not 0 <= minute <= 59:
        raise ValueError(f"Invalid time format: {value!r}")
    if period == "PM" and hour != 12:
        hour += 12
    if period == "AM" and hour == 12:
        hour = 0
    return hour, minute


def visual_to_cron(frequency: str, config: dict[str, Any]) -> str:
    if frequency == "hourly":
        minute = config.get("on_minute")
        if not isinstance(minute, int) or not 0 <= minute <= 59:
            raise ValueError("on_minute must be between 0 and 59")
        return f"{minute} * * * *"
    hour, minute = convert_12h_to_24h(str(config.get("time") or ""))
    if frequency == "daily":
        return f"{minute} {hour} * * *"
    if frequency == "weekly":
        weekdays = config.get("weekdays")
        mapping = {"sun": "0", "mon": "1", "tue": "2", "wed": "3", "thu": "4", "fri": "5", "sat": "6"}
        if not isinstance(weekdays, list) or not weekdays or any(day not in mapping for day in weekdays):
            raise ValueError("At least one valid weekday is required")
        values = sorted({mapping[day] for day in weekdays})
        return f"{minute} {hour} * * {','.join(values)}"
    if frequency == "monthly":
        days = config.get("monthly_days")
        if not isinstance(days, list) or not days:
            raise ValueError("At least one monthly day is required")
        numeric = []
        has_last = False
        for day in days:
            if day == "last":
                has_last = True
            elif not isinstance(day, int) or not 1 <= day <= 31:
                raise ValueError("Monthly days must be between 1 and 31, or 'last'")
            else:
                numeric.append(day)
        values = [str(day) for day in sorted(set(numeric))]
        if has_last:
            values.append("L")
        return f"{minute} {hour} {','.join(values)} * *"
    raise ValueError(f"Unsupported schedule frequency: {frequency}")


def schedule_cron(config: dict[str, Any]) -> str:
    mode = config.get("mode") or "visual"
    if mode == "cron":
        expression = config.get("cron_expression")
        if not isinstance(expression, str) or not expression.strip():
            raise ValueError("Cron expression is required for cron mode")
        return expression.strip()
    if mode == "visual":
        return visual_to_cron(str(config.get("frequency") or ""), config.get("visual_config") or {})
    raise ValueError(f"Unsupported schedule mode: {mode}")


def calculate_next_run_at(expression: str, timezone: str, base_time: datetime) -> datetime:
    parts = expression.strip().split()
    if len(parts) != 5 and not expression.startswith("@"):
        raise ValueError("Cron expression must have five fields or be a predefined expression")
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, OSError) as error:
        raise ValueError(f"Invalid timezone: {timezone!r}") from error
    try:
        next_run = croniter(expression, base_time.astimezone(zone)).get_next(datetime)
    except (CroniterBadCronError, CroniterBadDateError, ValueError) as error:
        raise ValueError(f"Invalid cron expression: {expression!r}") from error
    return next_run.astimezone(UTC)


class WebhookNotFound(Exception):
    pass


class WebhookRequestError(ValueError):
    pass


class TriggerService:
    def __init__(self, store: Store, runs: RunService) -> None:
        self.store = store
        self.runs = runs

    @staticmethod
    def trigger_nodes(graph) -> list[dict[str, Any]]:
        return [
            {"id": node["id"], "config": node["data"]}
            for node in graph.to_json_object()["nodes"]
            if node.get("data", {}).get("type") in TRIGGER_TYPES
        ]

    def definitions(self, workflow: PublishedWorkflow, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now(UTC)
        result = []
        for node in self.trigger_nodes(workflow.graph):
            config = node["config"]
            trigger_type = config["type"]
            next_run_at = None
            if trigger_type == "trigger-schedule":
                expression = schedule_cron(config)
                next_run_at = int(
                    calculate_next_run_at(expression, str(config.get("timezone") or "UTC"), now).timestamp()
                )
            elif trigger_type == "trigger-webhook":
                self.validate_webhook(config)
            result.append(
                {
                    "node_id": node["id"],
                    "trigger_type": trigger_type,
                    "config": config,
                    "next_run_at": next_run_at,
                }
            )
        return result

    def validate_graph(self, graph) -> None:
        nodes = self.trigger_nodes(graph)
        for node in nodes:
            config = node["config"]
            if config["type"] == "trigger-schedule":
                calculate_next_run_at(
                    schedule_cron(config),
                    str(config.get("timezone") or "UTC"),
                    datetime.now(UTC),
                )
            else:
                self.validate_webhook(config)

    @staticmethod
    def validate_webhook(config: dict[str, Any]) -> None:
        data = WebhookData.model_validate(config)
        if data.method not in {"get", "post", "put", "patch", "delete", "head"}:
            raise ValueError(f"Unsupported webhook method: {data.method}")
        for group in (data.headers, data.params, data.body):
            names = [parameter.name for parameter in group]
            if any(not name for name in names) or len(set(names)) != len(names):
                raise ValueError("Webhook parameter names must be non-empty and unique")
        if any(parameter.type.value == "file" for parameter in data.body):
            raise ValueError("Webhook file inputs are not supported")

    def sync(self, workflow: PublishedWorkflow) -> None:
        definitions = self.definitions(workflow)
        self.store.sync_workflow_triggers(
            app_id=workflow.app_id,
            workflow_id=workflow.id,
            definitions=definitions,
            actor=workflow.created_by,
        )

    def list(self, app_id: AppId) -> list[dict[str, Any]]:
        published = self.store.get_published(app_id)
        if published is None:
            return []
        triggers = self.store.list_workflow_triggers(app_id, published.id)
        return [
            {
                "id": trigger.id,
                "trigger_type": trigger.trigger_type,
                "title": trigger.config.get("title") or trigger.trigger_type,
                "node_id": trigger.node_id,
                "provider_name": "Schedule" if trigger.trigger_type == "trigger-schedule" else "Webhook",
                "icon": "",
                "status": trigger.status,
                "created_at": datetime.fromtimestamp(trigger.created_at, UTC),
                "updated_at": datetime.fromtimestamp(trigger.updated_at, UTC),
            }
            for trigger in triggers
        ]

    def set_enabled(self, app_id: AppId, trigger_id: str, enabled: bool) -> dict[str, Any] | None:
        trigger = self.store.set_workflow_trigger_enabled(app_id, trigger_id, enabled)
        if trigger is None:
            return None
        return next(item for item in self.list(app_id) if item["id"] == trigger.id)

    def webhook(self, app_id: AppId, node_id: str, actor: Actor) -> dict[str, Any]:
        draft = self.store.get_draft(app_id)
        if draft is None:
            raise WebhookNotFound
        node = next(
            (
                node
                for node in self.trigger_nodes(draft.graph)
                if node["id"] == node_id and node["config"]["type"] == "trigger-webhook"
            ),
            None,
        )
        if node is None:
            raise WebhookNotFound
        trigger = self.store.ensure_webhook(
            app_id=app_id,
            workflow_id=draft.workflow_id,
            node_id=node_id,
            config=node["config"],
            actor=actor,
        )
        return self.webhook_response(trigger)

    @staticmethod
    def webhook_response(trigger: WorkflowTrigger) -> dict[str, Any]:
        import os

        base_url = os.environ.get("CONSOLE_API_URL", "http://127.0.0.1:5001").rstrip("/")
        return {
            "id": trigger.id,
            "webhook_id": trigger.webhook_id,
            "webhook_url": f"{base_url}/triggers/webhook/{trigger.webhook_id}",
            "webhook_debug_url": f"{base_url}/triggers/webhook-debug/{trigger.webhook_id}",
            "node_id": trigger.node_id,
            "created_at": datetime.fromtimestamp(trigger.created_at, UTC),
        }

    async def execute_webhook(self, request: Request, webhook_id: str, *, debug: bool) -> JSONResponse:
        trigger = self.store.get_draft_webhook(webhook_id) if debug else self.store.get_enabled_webhook(webhook_id)
        if trigger is None:
            raise WebhookNotFound
        if debug:
            draft = self.store.get_draft(trigger.app_id)
            node = (
                next(
                    (
                        item
                        for item in self.trigger_nodes(draft.graph)
                        if item["id"] == trigger.node_id and item["config"]["type"] == "trigger-webhook"
                    ),
                    None,
                )
                if draft is not None
                else None
            )
            if node is None:
                raise WebhookNotFound
            trigger = replace(trigger, workflow_id=draft.workflow_id, config=node["config"])
        data = await self.parse_webhook_request(request, trigger.config)
        inputs = {
            "webhook_data": data,
            "webhook_headers": data["headers"],
            "webhook_query_params": data["query_params"],
            "webhook_body": data["body"],
        }
        actor = trigger.created_by
        if debug:
            session = self.runs.start(trigger.app_id, inputs, actor, root_node_id=trigger.node_id)
        else:
            session = self.runs.start_published(
                trigger.app_id,
                inputs,
                actor,
                root_node_id=trigger.node_id,
            )
        return JSONResponse(
            self.webhook_response_body(trigger.config),
            status_code=int(trigger.config.get("status_code") or 200),
            headers={"X-Workflow-Run-Id": session.workflow_run_id},
        )

    @staticmethod
    def webhook_response_body(config: dict[str, Any]) -> dict[str, Any]:
        body = str(config.get("response_body") or "")
        if not body:
            return {"status": "success", "message": "Webhook processed successfully"}
        if body.strip().startswith(("{", "[")):
            try:
                value = json.loads(body)
                if isinstance(value, dict):
                    return value
                return {"data": value}
            except json.JSONDecodeError:
                pass
        return {"message": body}

    async def parse_webhook_request(self, request: Request, config: dict[str, Any]) -> dict[str, Any]:
        data = WebhookData.model_validate(config)
        if request.method.lower() != data.method:
            raise WebhookRequestError(f"Expected {data.method.upper()} request")
        query_params: dict[str, Any] = dict(request.query_params)
        content_type = data.content_type
        body: dict[str, Any] = {}
        files: dict[str, Any] = {}
        raw = await request.body()
        if content_type == "application/json":
            if raw:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError as error:
                    raise WebhookRequestError("Request body must be valid JSON") from error
                if not isinstance(parsed, dict):
                    raise WebhookRequestError("JSON request body must be an object")
                body = parsed
        elif content_type == "text/plain":
            body = {"raw": raw.decode("utf-8", errors="replace")}
        elif content_type in {"application/x-www-form-urlencoded", "multipart/form-data"}:
            form = await request.form()
            if any(hasattr(value, "filename") for value in form.values()):
                raise WebhookRequestError("Webhook file uploads are not supported")
            body = dict(form)
        elif content_type == "application/octet-stream":
            raise WebhookRequestError("Binary webhook uploads are not supported")
        else:
            raise WebhookRequestError(f"Unsupported webhook content type: {content_type}")
        result = {
            "method": request.method.upper(),
            "headers": dict(request.headers),
            "query_params": query_params,
            "body": body,
            "files": files,
        }
        self.validate_webhook_inputs(result, data)
        return result

    @staticmethod
    def validate_webhook_inputs(data: dict[str, Any], config: WebhookData) -> None:
        groups = (
            (config.headers, data["headers"], True),
            (config.params, data["query_params"], False),
            (config.body, data["body"], False),
        )
        for parameters, values, is_headers in groups:
            normalized = {str(key).lower(): value for key, value in values.items()} if is_headers else values
            for parameter in parameters:
                key = parameter.name.lower() if is_headers else parameter.name
                value = normalized.get(key)
                if parameter.required and (value is None or value == ""):
                    raise WebhookRequestError(f"Required webhook parameter is missing: {parameter.name}")
                if value is not None:
                    data["headers" if is_headers else "query_params" if parameters is config.params else "body"][
                        key
                    ] = coerce_webhook_value(value, parameter.type.value)

    @staticmethod
    def scheduled_inputs(trigger: WorkflowTrigger, run_key: str) -> dict[str, Any]:
        return {
            "trigger_type": "schedule",
            "scheduled_at": datetime.fromtimestamp(int(run_key), UTC).isoformat(),
        }


class TriggerScheduler:
    def __init__(self, store: Store, runs: RunService, *, interval_seconds: float = 5.0) -> None:
        self.store = store
        self.runs = runs
        self.interval_seconds = interval_seconds
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopped.clear()
        self._thread = threading.Thread(target=self._loop, name="workflow-trigger-scheduler", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=max(self.interval_seconds + 1, 2))
            self._thread = None

    def _loop(self) -> None:
        while not self._stopped.is_set():
            try:
                self.poll_once()
            except Exception:
                logger.exception("Workflow trigger polling failed")
            self._stopped.wait(self.interval_seconds)

    def poll_once(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        now_timestamp = int(now.timestamp())
        dispatched = 0
        for trigger in self.store.list_due_schedules(now_timestamp):
            if trigger.next_run_at is None:
                continue
            run_key = str(trigger.next_run_at)
            try:
                next_run = calculate_next_run_at(
                    schedule_cron(trigger.config),
                    str(trigger.config.get("timezone") or "UTC"),
                    now,
                )
                claimed = self.store.claim_schedule(
                    trigger_id=trigger.id,
                    expected_next_run_at=trigger.next_run_at,
                    run_key=run_key,
                    next_run_at=int(next_run.timestamp()),
                )
                if not claimed:
                    continue
                session = self.runs.start_published(
                    trigger.app_id,
                    TriggerService.scheduled_inputs(trigger, run_key),
                    trigger.created_by,
                    root_node_id=trigger.node_id,
                )
                session.close()
                dispatched += 1
            except Exception:
                logger.exception("Scheduled workflow dispatch failed for trigger %s", trigger.id)
        return dispatched


def coerce_webhook_value(value: Any, value_type: str) -> Any:
    if value_type == "number":
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise WebhookRequestError(f"Expected a number, got {value!r}") from error
        return int(number) if number.is_integer() else number
    if value_type == "boolean":
        if isinstance(value, bool):
            return value
        if str(value).lower() in {"true", "1"}:
            return True
        if str(value).lower() in {"false", "0"}:
            return False
        raise WebhookRequestError(f"Expected a boolean, got {value!r}")
    if value_type == "object" or value_type.startswith("array["):
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError) as error:
            raise WebhookRequestError(f"Expected JSON for {value_type}") from error
    return value
