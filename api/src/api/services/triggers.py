from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadCronError, CroniterBadDateError, croniter
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.configs import app_config
from api.core.nodes.trigger_webhook.entities import WebhookData
from api.models.app import App
from api.models.entities import Actor, AppId, GraphDocument, PublishedWorkflow, WorkflowTrigger
from api.models.workflow import WorkflowDraft
from api.models.workflow_trigger import WorkflowTrigger as WorkflowTriggerModel

from .base_service import BaseService

if TYPE_CHECKING:
    from api.services.run_service import RunService

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


class TriggerService(BaseService):
    def __init__(self, db: AsyncSession, runs: RunService) -> None:
        super().__init__(db)
        self.runs = runs

    @staticmethod
    def _from_model(trigger: WorkflowTriggerModel) -> WorkflowTrigger:
        return WorkflowTrigger(
            id=trigger.id,
            app_id=AppId(trigger.app_id),
            workflow_id=trigger.workflow_id,
            node_id=trigger.node_id,
            trigger_type=trigger.trigger_type,
            status=trigger.status,
            webhook_id=trigger.webhook_id,
            config=json.loads(trigger.config_json),
            created_by=Actor(**json.loads(trigger.created_by_json)),
            next_run_at=trigger.next_run_at,
            last_run_key=trigger.last_run_key,
            created_at=trigger.created_at,
            updated_at=trigger.updated_at,
        )

    @staticmethod
    def trigger_nodes(graph: GraphDocument) -> list[dict[str, Any]]:
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

    def validate_graph(self, graph: GraphDocument) -> None:
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

    async def sync(self, workflow: PublishedWorkflow) -> None:
        definitions = self.definitions(workflow)
        now = int(time.time())
        triggers = (
            (await self.db.execute(select(WorkflowTriggerModel).where(WorkflowTriggerModel.app_id == workflow.app_id)))
            .scalars()
            .all()
        )
        existing = {trigger.node_id: trigger for trigger in triggers}
        node_ids = {definition["node_id"] for definition in definitions}
        for node_id in set(existing) - node_ids:
            await self.db.delete(existing[node_id])
        draft_workflow_id = await self.db.scalar(
            select(WorkflowDraft.workflow_id).where(WorkflowDraft.app_id == workflow.app_id)
        )
        for definition in definitions:
            node_id = definition["node_id"]
            current = existing.get(node_id)
            if current is None:
                self.db.add(
                    WorkflowTriggerModel(
                        id=str(uuid4()),
                        app_id=workflow.app_id,
                        workflow_id=workflow.id,
                        node_id=node_id,
                        trigger_type=definition["trigger_type"],
                        status="enabled",
                        webhook_id=(
                            secrets.token_urlsafe(24) if definition["trigger_type"] == "trigger-webhook" else None
                        ),
                        config_json=json.dumps(definition["config"]),
                        created_by_json=json.dumps(asdict(workflow.created_by)),
                        next_run_at=definition.get("next_run_at"),
                        last_run_key=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
                continue
            was_draft = current.workflow_id == draft_workflow_id
            current.workflow_id = workflow.id
            current.trigger_type = definition["trigger_type"]
            current.config_json = json.dumps(definition["config"])
            current.created_by_json = json.dumps(asdict(workflow.created_by))
            current.next_run_at = definition.get("next_run_at")
            current.updated_at = now
            if was_draft:
                current.status = "enabled"
            if current.trigger_type == "trigger-webhook" and current.webhook_id is None:
                current.webhook_id = secrets.token_urlsafe(24)
        await self.db.commit()

    @staticmethod
    def _list_item(trigger: WorkflowTrigger) -> dict[str, Any]:
        return {
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

    async def list(self, app_id: AppId) -> list[dict[str, Any]]:
        triggers = (
            (
                await self.db.execute(
                    select(WorkflowTriggerModel)
                    .join(App, App.published_workflow_id == WorkflowTriggerModel.workflow_id)
                    .where(WorkflowTriggerModel.app_id == app_id)
                    .order_by(WorkflowTriggerModel.created_at)
                )
            )
            .scalars()
            .all()
        )
        return [self._list_item(self._from_model(trigger)) for trigger in triggers]

    async def set_enabled(self, app_id: AppId, trigger_id: str, enabled: bool) -> dict[str, Any] | None:
        result = await self.db.execute(
            update(WorkflowTriggerModel)
            .where(
                WorkflowTriggerModel.app_id == app_id,
                WorkflowTriggerModel.id == trigger_id,
            )
            .values(status="enabled" if enabled else "disabled", updated_at=int(time.time()))
        )
        if result.rowcount != 1:
            await self.db.rollback()
            return None
        await self.db.commit()
        return next((item for item in await self.list(app_id) if item["id"] == trigger_id), None)

    async def webhook(self, app_id: AppId, node_id: str, actor: Actor) -> dict[str, Any]:
        draft = await self.db.get(WorkflowDraft, app_id)
        if draft is None:
            raise WebhookNotFound
        graph = GraphDocument.parse(json.loads(draft.graph_json))
        node = next(
            (
                node
                for node in self.trigger_nodes(graph)
                if node["id"] == node_id and node["config"]["type"] == "trigger-webhook"
            ),
            None,
        )
        if node is None:
            raise WebhookNotFound
        trigger = await self.db.scalar(
            select(WorkflowTriggerModel).where(
                WorkflowTriggerModel.app_id == app_id,
                WorkflowTriggerModel.node_id == node_id,
            )
        )
        now = int(time.time())
        if trigger is None:
            trigger = WorkflowTriggerModel(
                id=str(uuid4()),
                app_id=app_id,
                workflow_id=draft.workflow_id,
                node_id=node_id,
                trigger_type="trigger-webhook",
                status="disabled",
                webhook_id=secrets.token_urlsafe(24),
                config_json=json.dumps(node["config"]),
                created_by_json=json.dumps(asdict(actor)),
                next_run_at=None,
                last_run_key=None,
                created_at=now,
                updated_at=now,
            )
            self.db.add(trigger)
        else:
            trigger.workflow_id = draft.workflow_id
            trigger.trigger_type = "trigger-webhook"
            trigger.config_json = json.dumps(node["config"])
            trigger.updated_at = now
            if trigger.webhook_id is None:
                trigger.webhook_id = secrets.token_urlsafe(24)
        await self.db.flush()
        result = self._from_model(trigger)
        await self.db.commit()
        return self.webhook_response(result)

    @staticmethod
    def webhook_response(trigger: WorkflowTrigger) -> dict[str, Any]:
        base_url = app_config.PUBLIC_API_URL.rstrip("/")
        return {
            "id": trigger.id,
            "webhook_id": trigger.webhook_id,
            "webhook_url": f"{base_url}/triggers/webhook/{trigger.webhook_id}",
            "webhook_debug_url": f"{base_url}/triggers/webhook-debug/{trigger.webhook_id}",
            "node_id": trigger.node_id,
            "created_at": datetime.fromtimestamp(trigger.created_at, UTC),
        }

    async def execute_webhook(self, request: Request, webhook_id: str, *, debug: bool) -> JSONResponse:
        statement = select(WorkflowTriggerModel)
        if debug:
            statement = statement.join(WorkflowDraft, WorkflowDraft.app_id == WorkflowTriggerModel.app_id)
        else:
            statement = statement.join(App, App.published_workflow_id == WorkflowTriggerModel.workflow_id).where(
                WorkflowTriggerModel.status == "enabled"
            )
        statement = statement.where(
            WorkflowTriggerModel.webhook_id == webhook_id,
            WorkflowTriggerModel.trigger_type == "trigger-webhook",
        )
        model = await self.db.scalar(statement)
        if model is None:
            raise WebhookNotFound
        trigger = self._from_model(model)
        if debug:
            draft = await self.db.get(WorkflowDraft, trigger.app_id)
            graph = GraphDocument.parse(json.loads(draft.graph_json)) if draft is not None else None
            node = (
                next(
                    (
                        item
                        for item in self.trigger_nodes(graph)
                        if item["id"] == trigger.node_id and item["config"]["type"] == "trigger-webhook"
                    ),
                    None,
                )
                if graph is not None
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
            session = await self.runs.start_draft(trigger.app_id, inputs, actor, root_node_id=trigger.node_id)
        else:
            session = await self.runs.start_published(
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
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        runs_factory: Callable[[AsyncSession], RunService],
        *,
        interval_seconds: float = 5.0,
    ) -> None:
        self.session_factory = session_factory
        self.runs_factory = runs_factory
        self.interval_seconds = interval_seconds
        self._stopped = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._loop(), name="workflow-trigger-scheduler")

    async def close(self) -> None:
        self._stopped.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._stopped.is_set():
            try:
                await self.poll_once()
            except Exception:
                logger.exception("Workflow trigger polling failed")
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue

    async def poll_once(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        now_timestamp = int(now.timestamp())
        dispatched = 0
        async with self.session_factory() as db:
            models = (
                (
                    await db.execute(
                        select(WorkflowTriggerModel)
                        .join(App, App.published_workflow_id == WorkflowTriggerModel.workflow_id)
                        .where(
                            WorkflowTriggerModel.trigger_type == "trigger-schedule",
                            WorkflowTriggerModel.status == "enabled",
                            WorkflowTriggerModel.next_run_at.is_not(None),
                            WorkflowTriggerModel.next_run_at <= now_timestamp,
                        )
                        .order_by(WorkflowTriggerModel.next_run_at)
                        .limit(100)
                    )
                )
                .scalars()
                .all()
            )
            triggers = [TriggerService._from_model(trigger) for trigger in models]
            for trigger in triggers:
                if trigger.next_run_at is None:
                    continue
                run_key = str(trigger.next_run_at)
                try:
                    next_run = calculate_next_run_at(
                        schedule_cron(trigger.config),
                        str(trigger.config.get("timezone") or "UTC"),
                        now,
                    )
                    result = await db.execute(
                        update(WorkflowTriggerModel)
                        .where(
                            WorkflowTriggerModel.id == trigger.id,
                            WorkflowTriggerModel.status == "enabled",
                            WorkflowTriggerModel.next_run_at == trigger.next_run_at,
                        )
                        .values(
                            last_run_key=run_key,
                            next_run_at=int(next_run.timestamp()),
                            updated_at=int(time.time()),
                        )
                    )
                    await db.commit()
                    if result.rowcount != 1:
                        continue
                    session = await self.runs_factory(db).start_published(
                        trigger.app_id,
                        TriggerService.scheduled_inputs(trigger, run_key),
                        trigger.created_by,
                        root_node_id=trigger.node_id,
                    )
                    session.close()
                    dispatched += 1
                except Exception:
                    await db.rollback()
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
