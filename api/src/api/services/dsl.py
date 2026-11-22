from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import redis
import yaml
from packaging.version import InvalidVersion
from packaging.version import parse as parse_version
from sqlalchemy import select

from api.models import WorkflowDraft, WorkflowVersion
from api.models.entities import Draft, DraftHash, GraphDocument, PublishedWorkflow, SaveDraft
from api.services.workflows import (
    DraftNotFound,
    InvalidWorkflow,
    WorkflowService,
    draft_from_model,
    published_workflow_from_model,
)

from .base_service import BaseService

if TYPE_CHECKING:
    from api.services.auth import Principal

CURRENT_DSL_VERSION = "0.7.0"
DSL_MAX_SIZE = 10 * 1024 * 1024
PENDING_IMPORT_TTL_SECONDS = 10 * 60


class WorkflowVersionNotFound(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PreparedImport:
    graph: GraphDocument
    features: Mapping[str, Any]
    environment_variables: tuple[Mapping[str, Any], ...]
    conversation_variables: tuple[Mapping[str, Any], ...]
    app_name: str | None


@dataclass(frozen=True, slots=True)
class PendingImport:
    id: str
    app_id: str
    account_id: str
    imported_version: str
    prepared: PreparedImport
    expected_hash: DraftHash | None
    expires_at: float


class DslService(BaseService):
    def __init__(self, workflows: WorkflowService, redis_client: redis.Redis | None = None) -> None:
        super().__init__(workflows.db)
        self._workflows = workflows
        self._redis = redis_client
        self._pending: dict[str, PendingImport] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _pending_key(import_id: str) -> str:
        return f"workflow:imports:{import_id}"

    def _write_pending(self, pending: PendingImport) -> None:
        if self._redis is None:
            with self._lock:
                self._purge_expired()
                self._pending[pending.id] = pending
            return
        document = {
            "id": pending.id,
            "app_id": pending.app_id,
            "account_id": pending.account_id,
            "imported_version": pending.imported_version,
            "expected_hash": pending.expected_hash,
            "expires_at": pending.expires_at,
            "prepared": {
                "graph": pending.prepared.graph.to_json_object(),
                "features": dict(pending.prepared.features),
                "environment_variables": list(pending.prepared.environment_variables),
                "conversation_variables": list(pending.prepared.conversation_variables),
                "app_name": pending.prepared.app_name,
            },
        }
        self._redis.set(self._pending_key(pending.id), json.dumps(document), ex=PENDING_IMPORT_TTL_SECONDS)

    def _read_pending(self, import_id: str) -> PendingImport | None:
        if self._redis is None:
            with self._lock:
                return self._pending.get(import_id)
        raw = self._redis.get(self._pending_key(import_id))
        if raw is None:
            return None
        data = json.loads(raw)
        prepared = data["prepared"]
        return PendingImport(
            id=data["id"],
            app_id=data["app_id"],
            account_id=data["account_id"],
            imported_version=data["imported_version"],
            prepared=PreparedImport(
                graph=GraphDocument.parse(prepared["graph"]),
                features=prepared["features"],
                environment_variables=tuple(prepared["environment_variables"]),
                conversation_variables=tuple(prepared["conversation_variables"]),
                app_name=prepared["app_name"],
            ),
            expected_hash=DraftHash(data["expected_hash"]) if data["expected_hash"] is not None else None,
            expires_at=data["expires_at"],
        )

    def _delete_pending(self, import_id: str) -> None:
        if self._redis is not None:
            self._redis.delete(self._pending_key(import_id))
        else:
            with self._lock:
                self._pending.pop(import_id, None)

    async def export(
        self,
        app_id: str,
        *,
        app_name: str,
        include_secret: bool,
        workflow_id: str | None,
    ) -> str:
        workflow = await self._export_source(app_id, workflow_id)
        environment_variables = []
        for raw_variable in workflow.environment_variables:
            variable = dict(raw_variable)
            if variable.get("value_type") == "secret" and not include_secret:
                variable["value"] = ""
            environment_variables.append(variable)
        document = {
            "version": CURRENT_DSL_VERSION,
            "kind": "app",
            "app": {
                "name": app_name,
                "mode": "workflow",
                "icon": None,
                "icon_type": None,
                "icon_background": None,
                "description": "Standalone workflow",
                "use_icon_as_answer_icon": False,
            },
            "dependencies": [],
            "workflow": {
                "graph": workflow.graph.to_json_object(),
                "features": dict(workflow.features),
                "environment_variables": environment_variables,
                "conversation_variables": [dict(variable) for variable in workflow.conversation_variables],
            },
        }
        return yaml.safe_dump(document, allow_unicode=True, sort_keys=False)

    async def _export_source(self, app_id: str, workflow_id: str | None) -> Draft | PublishedWorkflow:
        if workflow_id is None:
            draft = await self.db.get(WorkflowDraft, app_id)
            if draft is None:
                raise DraftNotFound
            return draft_from_model(draft)
        workflow = await self.db.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.app_id == app_id,
                WorkflowVersion.id == workflow_id,
            )
        )
        if workflow is None:
            raise WorkflowVersionNotFound
        return published_workflow_from_model(workflow)

    async def stage_or_import(
        self,
        *,
        app_id: str,
        content: str,
        name: str | None,
        principal: Principal,
    ) -> dict[str, Any]:
        import_id = str(uuid4())
        if not content:
            return self.failed(import_id, "yaml_content is required when import_mode is yaml-content", app_id=app_id)
        if len(content.encode("utf-8")) > DSL_MAX_SIZE:
            return self.failed(import_id, "File size exceeds the limit of 10MB", app_id=app_id)
        try:
            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                raise ValueError("Invalid YAML format: content must be a mapping")
            if not data.get("version"):
                data["version"] = "0.1.0"
            if data.get("kind") != "app":
                data["kind"] = "app"
            imported_version = data["version"]
            if not isinstance(imported_version, str):
                raise ValueError("Invalid version type, expected a string")
            status = self.compatibility(imported_version)
            if status == "failed":
                return self.failed(import_id, "Invalid DSL version", app_id=app_id, imported_version=imported_version)
            prepared = await self._prepare(data, name)
        except (yaml.YAMLError, InvalidWorkflow, ValueError) as error:
            return self.failed(import_id, str(error), app_id=app_id)

        current = await self.db.get(WorkflowDraft, app_id)
        expected_hash = DraftHash(current.hash) if current is not None else None
        if status == "pending":
            pending = PendingImport(
                id=import_id,
                app_id=app_id,
                account_id=principal.account_id,
                imported_version=imported_version,
                prepared=prepared,
                expected_hash=expected_hash,
                expires_at=time.time() + PENDING_IMPORT_TTL_SECONDS,
            )
            self._write_pending(pending)
            return self.response(import_id, status, app_id, imported_version)

        await self._commit(
            import_id=import_id,
            app_id=app_id,
            imported_version=imported_version,
            prepared=prepared,
            expected_hash=expected_hash,
            principal=principal,
            status=status,
        )
        return self.response(import_id, status, app_id, imported_version)

    async def _prepare(self, data: Mapping[str, Any], name: str | None) -> PreparedImport:
        app = data.get("app")
        if not isinstance(app, Mapping):
            raise ValueError("Missing app data in YAML content")
        if app.get("mode") != "workflow":
            raise ValueError("Only workflow DSLs can overwrite this app")
        app_name = name or app.get("name")
        if app_name is not None and not isinstance(app_name, str):
            raise ValueError("App name must be a string")
        workflow = data.get("workflow")
        if not isinstance(workflow, Mapping):
            raise ValueError("Missing workflow data")
        graph = GraphDocument.parse(workflow.get("graph"))
        await self._workflows.validate_graph(graph)
        features = workflow.get("features", {})
        if not isinstance(features, Mapping):
            raise ValueError("Workflow features must be a mapping")
        raw_environment = workflow.get("environment_variables", [])
        raw_conversation = workflow.get("conversation_variables", [])
        if not isinstance(raw_environment, list) or not all(isinstance(item, Mapping) for item in raw_environment):
            raise ValueError("Environment variables must be a list of mappings")
        if not isinstance(raw_conversation, list) or not all(isinstance(item, Mapping) for item in raw_conversation):
            raise ValueError("Conversation variables must be a list of mappings")
        return PreparedImport(
            graph=graph,
            features=dict(features),
            environment_variables=self._workflows.normalize_environment_variables(raw_environment),
            conversation_variables=tuple(dict(item) for item in raw_conversation),
            app_name=app_name or None,
        )

    @staticmethod
    def compatibility(imported_version: str) -> str:
        try:
            imported = parse_version(imported_version)
            current = parse_version(CURRENT_DSL_VERSION)
        except InvalidVersion:
            return "failed"
        if imported > current or imported.major < current.major:
            return "pending"
        if imported.minor < current.minor:
            return "completed-with-warnings"
        return "completed"

    def pending_import(self, import_id: str, principal: Principal) -> PendingImport | None:
        pending = self._read_pending(import_id)
        if pending is not None and pending.expires_at <= time.time():
            self._delete_pending(import_id)
            return None
        if pending is None or pending.account_id != principal.account_id:
            return None
        return pending

    async def confirm(self, import_id: str, principal: Principal) -> dict[str, Any]:
        pending = self.pending_import(import_id, principal)
        if pending is None:
            return self.failed(import_id, "Import information expired or does not exist")
        await self._commit(
            import_id=import_id,
            app_id=pending.app_id,
            imported_version=pending.imported_version,
            prepared=pending.prepared,
            expected_hash=pending.expected_hash,
            principal=principal,
            status="completed",
        )
        self._delete_pending(import_id)
        return self.response(import_id, "completed", pending.app_id, pending.imported_version)

    async def _commit(
        self,
        *,
        import_id: str,
        app_id: str,
        imported_version: str,
        prepared: PreparedImport,
        expected_hash: DraftHash | None,
        principal: Principal,
        status: str,
    ) -> None:
        await self._workflows.save_draft(
            SaveDraft(
                app_id=app_id,
                graph=prepared.graph,
                features=prepared.features,
                conversation_variables=prepared.conversation_variables,
                expected_hash=expected_hash,
                force=False,
                actor=principal.actor,
                environment_variables=prepared.environment_variables,
                app_name=prepared.app_name,
            )
        )

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [import_id for import_id, pending in self._pending.items() if pending.expires_at <= now]
        for import_id in expired:
            self._pending.pop(import_id, None)

    @staticmethod
    def response(import_id: str, status: str, app_id: str | None, imported_version: str = "") -> dict[str, Any]:
        return {
            "id": import_id,
            "status": status,
            "app_id": app_id,
            "app_mode": "workflow" if app_id else None,
            "current_dsl_version": CURRENT_DSL_VERSION,
            "imported_dsl_version": imported_version,
            "error": "",
            "warnings": [],
        }

    @staticmethod
    def failed(import_id: str, error: str, *, app_id: str | None = None, imported_version: str = "") -> dict[str, Any]:
        return {
            **DslService.response(import_id, "failed", app_id, imported_version),
            "error": error,
        }
