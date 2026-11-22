from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.configs import app_config
from api.core.graphon.nodes.human_input.entities import (
    Completed,
    Expired,
    HITLContext,
    HITLDecision,
    PauseRequested,
)
from api.core.graphon.variables.factory import build_segment
from api.core.graphon.variables.template_resolution import convert_template
from api.models.human_input import HumanInputForm
from api.schemas.human_input import HumanInputConfig

from .base_service import BaseService
from .files import FileService

ACTION_ID = "__action_id"
ACTION_VALUE = "__action_value"
RENDERED_CONTENT = "__rendered_content"
TIMEOUT_HANDLE = "__timeout"


class HumanInputError(ValueError):
    pass


class HumanInputNotFound(HumanInputError):
    pass


class HumanInputConflict(HumanInputError):
    pass


class HumanInputService(BaseService):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__(db)

    async def current_for_node(self, run_id: str, node_id: str, node_execution_id: str) -> HumanInputForm | None:
        return await self.db.scalar(
            select(HumanInputForm)
            .where(
                HumanInputForm.workflow_run_id == run_id,
                HumanInputForm.node_id == node_id,
                HumanInputForm.node_execution_id == node_execution_id,
            )
            .order_by(HumanInputForm.created_at.desc(), HumanInputForm.id.desc())
            .limit(1)
        )

    async def create(
        self,
        context: HITLContext,
        config: HumanInputConfig,
        actor_id: str,
        node_execution_id: str,
    ) -> HumanInputForm:
        rendered = convert_template(context.variable_pool, config.form_content).markdown
        resolved_config = self._resolve_options(config, context)
        defaults = {}
        for item in resolved_config.inputs:
            if item.type != "paragraph" or item.default is None or item.default.type != "variable":
                continue
            segment = context.variable_pool.get(item.default.selector)
            if segment is not None:
                defaults[item.output_variable_name] = segment.to_object()
        resolved_config = resolved_config.model_copy(update={"resolved_default_values": defaults})
        now = int(time.time())
        multiplier = 3600 if config.timeout_unit == "hour" else 86400
        form = HumanInputForm(
            id=str(uuid4()),
            access_token=secrets.token_urlsafe(32),
            app_id=self._app_id(context),
            workflow_run_id=context.workflow_execution_id,
            node_id=context.node_id,
            node_execution_id=node_execution_id,
            created_by_id=actor_id,
            definition_json=json.dumps(resolved_config.model_dump(mode="json"), ensure_ascii=False),
            rendered_content=rendered,
            status="waiting",
            expiration_time=now + config.timeout * multiplier,
            created_at=now,
            updated_at=now,
        )
        self.db.add(form)
        await self.db.commit()
        return form

    @staticmethod
    def _app_id(context: HITLContext) -> str:
        segment = context.variable_pool.get(("sys", "app_id"))
        value = segment.to_object() if segment is not None else None
        if not isinstance(value, str) or not value:
            raise ValueError("app_id is required for human input")
        return value

    @staticmethod
    def _resolve_options(config: HumanInputConfig, context: HITLContext) -> HumanInputConfig:
        inputs = []
        for item in config.inputs:
            if item.type != "select" or item.option_source.type != "variable":
                inputs.append(item)
                continue
            segment = context.variable_pool.get(item.option_source.selector)
            options = segment.to_object() if segment is not None else []
            if not isinstance(options, list) or not all(isinstance(option, str) for option in options):
                raise ValueError(f"Options for {item.output_variable_name} must be an array of strings")
            inputs.append(
                item.model_copy(
                    update={
                        "option_source": item.option_source.model_copy(update={"type": "constant", "value": options})
                    }
                )
            )
        return config.model_copy(update={"inputs": inputs})

    @staticmethod
    def config(form: HumanInputForm) -> HumanInputConfig:
        return HumanInputConfig.model_validate(json.loads(form.definition_json))

    def response(self, form: HumanInputForm) -> dict[str, Any]:
        config = self.config(form)
        return {
            "form_version": "1",
            "form_id": form.id,
            "node_id": form.node_id,
            "node_title": config.title or form.node_id,
            "form_content": form.rendered_content,
            "inputs": config.model_dump(mode="json")["inputs"],
            "actions": config.model_dump(mode="json")["user_actions"],
            "display_in_ui": True,
            "form_token": form.access_token,
            "resolved_default_values": self.config(form).resolved_default_values,
            "expiration_time": form.expiration_time,
            "status": form.status,
        }

    async def by_token(self, token: str) -> HumanInputForm:
        form = await self.db.scalar(select(HumanInputForm).where(HumanInputForm.access_token == token))
        if form is None:
            raise HumanInputNotFound("Human input form not found")
        return form

    def upload_token(self, form: HumanInputForm) -> dict[str, Any]:
        expires = min(int(time.time()) + 600, form.expiration_time)
        signature = hmac.new(
            app_config.FILE_SIGNING_KEY.encode(),
            f"human-input-upload:{form.id}:{expires}".encode(),
            hashlib.sha256,
        ).hexdigest()
        token = base64.urlsafe_b64encode(
            json.dumps({"form_id": form.id, "expires": expires, "signature": signature}).encode()
        ).decode()
        return {"upload_token": token, "expires_at": expires}

    async def verify_upload_token(self, token: str) -> HumanInputForm:
        try:
            payload = json.loads(base64.urlsafe_b64decode(token.encode()))
            form_id = payload["form_id"]
            expires = int(payload["expires"])
            signature = payload["signature"]
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise HumanInputError("Invalid upload token") from error
        form = await self.db.get(HumanInputForm, form_id)
        if form is None or form.status != "waiting" or expires <= int(time.time()):
            raise HumanInputError("Upload token is expired or unavailable")
        expected = hmac.new(
            app_config.FILE_SIGNING_KEY.encode(),
            f"human-input-upload:{form.id}:{expires}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(expected, signature):
            raise HumanInputError("Invalid upload token")
        return form

    async def validate_upload(self, form: HumanInputForm, file: Any) -> None:
        fields = [item for item in self.config(form).inputs if item.type in {"file", "file-list"}]
        file_type = FileService.file_type(file).value
        transfer_method = "remote_url" if file.source_url else "local_file"
        allowed = any(
            (not field.allowed_file_types or file_type in field.allowed_file_types)
            and (
                not field.allowed_file_extensions
                or file.extension.lower() in {extension.lower() for extension in field.allowed_file_extensions}
            )
            and (not field.allowed_file_upload_methods or transfer_method in field.allowed_file_upload_methods)
            for field in fields
        )
        if allowed:
            return
        files = FileService(self.db)
        await files.delete_content([file])
        await self.db.delete(file)
        await self.db.commit()
        raise HumanInputError("File type is not allowed by this form")

    async def submit(self, token: str, inputs: dict[str, object], action: str, actor_id: str) -> HumanInputForm:
        form = await self.by_token(token)
        if form.status != "waiting":
            raise HumanInputConflict("Human input form has already been completed")
        if form.expiration_time <= int(time.time()):
            raise HumanInputConflict("Human input form has expired")
        config = self.config(form)
        if action not in {item.id for item in config.user_actions}:
            raise HumanInputError("Invalid action")
        values = self._validate_inputs(config, inputs)
        now = int(time.time())
        result = await self.db.execute(
            update(HumanInputForm)
            .where(HumanInputForm.id == form.id, HumanInputForm.status == "waiting")
            .values(
                status="submitted",
                selected_action_id=action,
                submitted_data_json=json.dumps(values, ensure_ascii=False),
                submitted_by_id=actor_id,
                submitted_at=now,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            await self.db.rollback()
            raise HumanInputConflict("Human input form has already been completed")
        await self.db.commit()
        return await self.by_token(token)

    @staticmethod
    def _validate_inputs(config: HumanInputConfig, inputs: dict[str, object]) -> dict[str, object]:
        fields = {item.output_variable_name: item for item in config.inputs}
        extras = set(inputs) - set(fields)
        if extras:
            raise HumanInputError(f"Unknown form inputs: {', '.join(sorted(extras))}")
        values: dict[str, object] = {}
        for name, field in fields.items():
            value = inputs.get(name, "")
            if field.type in {"paragraph", "select"} and not isinstance(value, str):
                raise HumanInputError(f"{name} must be a string")
            if field.type == "select":
                options = field.option_source.value
                if value not in options:
                    raise HumanInputError(f"{name} must be one of the configured options")
            if field.type == "file" and not isinstance(value, dict):
                raise HumanInputError(f"{name} must be an uploaded file")
            if field.type == "file-list":
                if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
                    raise HumanInputError(f"{name} must be an array of uploaded files")
                if len(value) > field.number_limits:
                    raise HumanInputError(f"{name} exceeds the file count limit")
            values[name] = value
        return values

    async def decision(
        self,
        context: HITLContext,
        config: HumanInputConfig,
        actor_id: str,
        node_execution_id: str,
    ) -> HITLDecision:
        form = await self.current_for_node(context.workflow_execution_id, context.node_id, node_execution_id)
        if form is None:
            form = await self.create(context, config, actor_id, node_execution_id)
        if form.status == "waiting" and form.expiration_time <= int(time.time()):
            await self.db.execute(
                update(HumanInputForm)
                .where(HumanInputForm.id == form.id, HumanInputForm.status == "waiting")
                .values(status="timeout", updated_at=int(time.time()))
            )
            await self.db.commit()
            form.status = "timeout"
        if form.status == "timeout":
            return Expired(
                selected_handle=TIMEOUT_HANDLE,
                outputs=self._special_outputs("", "", form.rendered_content),
            )
        if form.status != "submitted":
            return PauseRequested(session_id=form.id)

        config = self.config(form)
        action = form.selected_action_id
        if action is None:
            raise HumanInputConflict("Submitted form has no selected action")
        submitted = json.loads(form.submitted_data_json or "{}")
        files = FileService(self.db)
        converted: dict[str, Any] = {}
        for field in config.inputs:
            value = submitted.get(field.output_variable_name, "")
            if field.type in {"file", "file-list"}:
                value = await files.convert_input(
                    value,
                    {"type": field.type, "required": False},
                    account_id=form.created_by_id,
                    app_id=form.app_id,
                )
            converted[field.output_variable_name] = value
        action_title = next(item.title for item in config.user_actions if item.id == action)
        outputs = dict(converted)
        outputs.update(self._special_outputs(action, action_title, form.rendered_content))
        return Completed(
            selected_handle=action,
            inputs={key: build_segment(value) for key, value in converted.items()},
            outputs={key: build_segment(value) for key, value in outputs.items()},
        )

    @staticmethod
    def _special_outputs(action_id: str, action_value: str, rendered: str) -> dict[str, str]:
        return {
            ACTION_ID: action_id,
            ACTION_VALUE: action_value,
            RENDERED_CONTENT: rendered,
        }

    async def expire_due(self) -> tuple[str, ...]:
        now = int(time.time())
        rows = (
            (
                await self.db.execute(
                    select(HumanInputForm.workflow_run_id).where(
                        HumanInputForm.status == "waiting",
                        HumanInputForm.expiration_time <= now,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return ()
        await self.db.execute(
            update(HumanInputForm)
            .where(HumanInputForm.status == "waiting", HumanInputForm.expiration_time <= now)
            .values(status="timeout", updated_at=now)
        )
        await self.db.commit()
        return tuple(dict.fromkeys(rows))


class HumanInputRuntime:
    def __init__(
        self,
        session_factory,
        *,
        config: HumanInputConfig,
        actor_id: str,
        database_url: str | None,
        events: list[dict[str, Any]] | None,
    ) -> None:
        self._session_factory = session_factory
        self._config = config
        self._actor_id = actor_id
        self._database_url = database_url
        self._events = events if events is not None else []
        self._node: Any = None

    def bind_node(self, node: Any) -> None:
        self._node = node

    def __call__(self, context: HITLContext) -> HITLDecision:
        if self._node is None:
            raise ValueError("Human input node is not bound")
        node_execution_id = self._node.execution_id
        engine = None
        session_factory = self._session_factory
        if self._database_url is not None and not self._database_url.startswith("sqlite"):
            from api.models.engine import create_session_factory

            engine, session_factory = create_session_factory(self._database_url)

        async def resolve() -> HITLDecision:
            try:
                async with session_factory() as db:
                    service = HumanInputService(db)
                    decision = await service.decision(
                        context,
                        self._config,
                        self._actor_id,
                        node_execution_id,
                    )
                    form = await service.current_for_node(
                        context.workflow_execution_id,
                        context.node_id,
                        node_execution_id,
                    )
                    if form is None:
                        raise HumanInputNotFound("Human input form was not persisted")
                    config = service.config(form)
                    if isinstance(decision, PauseRequested):
                        self._events.append({"event": "human_input_required", "data": service.response(form)})
                    elif isinstance(decision, Completed):
                        action_title = next(
                            (item.title for item in config.user_actions if item.id == form.selected_action_id),
                            "",
                        )
                        self._events.append(
                            {
                                "event": "human_input_form_filled",
                                "data": {
                                    "form_id": form.id,
                                    "node_id": form.node_id,
                                    "node_title": config.title or form.node_id,
                                    "rendered_content": form.rendered_content,
                                    "action_id": form.selected_action_id,
                                    "action_text": action_title,
                                    "submitted_data": json.loads(form.submitted_data_json or "{}"),
                                },
                            }
                        )
                    elif isinstance(decision, Expired):
                        self._events.append(
                            {
                                "event": "human_input_form_timeout",
                                "data": {
                                    "form_id": form.id,
                                    "node_id": form.node_id,
                                    "node_title": config.title or form.node_id,
                                    "expiration_time": form.expiration_time,
                                },
                            }
                        )
                    return decision
            finally:
                if engine is not None:
                    await engine.dispose()

        return asyncio.run(resolve())
