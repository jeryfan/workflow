from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from uuid6 import uuid7

from api.db.store import Store

if TYPE_CHECKING:
    from api.services.distributed_runs import CeleryRunBackend
from api.models.entities import (
    Actor,
    AppId,
    Draft,
    DraftHash,
    EnvironmentVariablePatch,
    ExecutionEvent,
    GraphDocument,
    RunFailed,
    RunIdentity,
    RunSnapshot,
    SaveDraft,
    TerminalStateConflict,
    WorkflowFinished,
)

LOCAL_ACTOR = Actor(id="local-user", name="Local User", email="local@example.invalid")


class DraftNotFound(Exception):
    pass


class InvalidWorkflow(Exception):
    pass


class WorkflowService:
    def __init__(self, store: Store, node_contract_path: Path) -> None:
        self.store = store
        contract = json.loads(node_contract_path.read_text(encoding="utf-8"))
        self._nodes = tuple(contract["nodes"])
        self._allowed_types = frozenset(node["type"] for node in self._nodes if node["execution_class"] is not None)

    def get_draft(self, app_id: AppId) -> Draft:
        draft = self.store.get_draft(app_id)
        if draft is None:
            raise DraftNotFound
        return draft

    def save_draft(self, command: SaveDraft) -> Draft:
        self.validate_graph(command.graph, force=command.force)
        return self.store.save_draft(command)

    def validate_graph(self, graph_document: GraphDocument, *, force: bool = False) -> None:
        graph = graph_document.to_json_object()
        if not graph.get("nodes", []) and not graph.get("edges", []) and not force:
            raise InvalidWorkflow("Saving an empty workflow requires force=true")
        for node_id, node in graph_document.nodes_by_id.items():
            node_type = node["data"]["type"]
            if node_type not in self._allowed_types:
                raise InvalidWorkflow(f"Unsupported node type: {node_type} at {node_id}")
            if node_type == "tool":
                data = node["data"]
                provider_id = data.get("provider_id")
                tool_name = data.get("tool_name")
                if data.get("provider_type") != "workflow":
                    raise InvalidWorkflow(f"Only workflow tools are supported at {node_id}")
                if (
                    not isinstance(provider_id, str)
                    or not provider_id
                    or not isinstance(tool_name, str)
                    or not tool_name
                ):
                    raise InvalidWorkflow(f"Tool node requires provider_id and tool_name at {node_id}")
                tool = self.store.get_workflow_tool(provider_id)
                if tool is None or tool.name != tool_name:
                    raise InvalidWorkflow(f"Workflow tool is unavailable at {node_id}")

    def default_configs(self) -> list[dict[str, Any]]:
        return [
            {"type": node["type"], "config": node["default_config"]}
            for node in self._nodes
            if node["execution_class"] is not None
        ]

    def default_config(self, node_type: str, query: str | None) -> dict[str, Any]:
        if query:
            parsed = json.loads(query)
            if not isinstance(parsed, dict):
                raise InvalidWorkflow("Default node config query must be a JSON object")
        for item in self.default_configs():
            if item["type"] == node_type:
                return item["config"]
        raise InvalidWorkflow(f"Unsupported node type: {node_type}")

    @staticmethod
    def _normalize_environment_variable(variable: Mapping[str, Any]) -> dict[str, Any]:
        variable_id = variable.get("id")
        if variable_id is not None and (not isinstance(variable_id, str) or not variable_id):
            raise ValueError("Environment variable id must be a non-empty string")
        name = variable.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Environment variable name must not be empty")
        value_type = variable.get("value_type")
        if value_type not in {"string", "number", "secret", "llm"}:
            raise ValueError(f"Unsupported environment variable type: {value_type}")
        return {
            "id": variable_id or str(uuid4()),
            "name": name,
            "value_type": value_type,
            "value": variable.get("value"),
            "description": variable.get("description") or "",
        }

    @staticmethod
    def normalize_environment_variables(variables: list[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
        normalized = tuple(WorkflowService._normalize_environment_variable(variable) for variable in variables)
        ids = [variable["id"] for variable in normalized]
        names = [variable["name"] for variable in normalized]
        if len(set(ids)) != len(ids) or len(set(names)) != len(names):
            raise ValueError("Environment variable ids and names must be unique")
        return normalized

    @staticmethod
    def _environment_patch(variables: list[Mapping[str, Any]], deleted_ids: list[str]) -> EnvironmentVariablePatch:
        if any(not variable.get("id") for variable in variables):
            raise ValueError("Patched environment variables require an id")
        return EnvironmentVariablePatch(
            upserts=tuple(WorkflowService._normalize_environment_variable(variable) for variable in variables),
            deleted_ids=tuple(deleted_ids),
        )

    def update_environment_variables(
        self,
        app_id: AppId,
        variables: list[dict[str, Any]],
        deleted_ids: list[str],
        patch: bool,
        actor: Actor,
    ) -> None:
        if deleted_ids and not patch:
            raise ValueError("deleted_environment_variable_ids requires patch=true")
        normalized = self.normalize_environment_variables(variables)
        command = (
            self._environment_patch(variables, deleted_ids)
            if patch
            else EnvironmentVariablePatch(upserts=normalized, deleted_ids=())
        )
        if not self.store.update_environment_variables(
            app_id,
            command,
            patch_mode=patch,
            actor=actor,
        ):
            raise DraftNotFound

    @staticmethod
    def parse_save_draft(app_id: str, payload: Any, actor: Actor) -> SaveDraft:
        patch = payload.environment_variable_patch
        environment_patch = (
            WorkflowService._environment_patch(
                [variable.model_dump() for variable in patch.environment_variables],
                patch.deleted_environment_variable_ids,
            )
            if patch is not None
            else None
        )
        return SaveDraft(
            app_id=AppId(app_id),
            graph=GraphDocument.parse(payload.graph),
            features=payload.features,
            conversation_variables=tuple(payload.conversation_variables),
            expected_hash=DraftHash(payload.hash) if payload.hash is not None else None,
            force=payload.force,
            actor=actor,
            environment_variable_patch=environment_patch,
        )


class RunNotFound(Exception):
    pass


class EngineExecution(Protocol):
    def events(self) -> Iterator[ExecutionEvent]: ...
    def request_stop(self, reason: str) -> None: ...


class Engine(Protocol):
    def start(self, snapshot: RunSnapshot) -> EngineExecution: ...


class RunSession:
    _sentinel = object()

    def __init__(self, identity: RunIdentity) -> None:
        self.identity = identity
        self._queue: queue.Queue[ExecutionEvent | object] = queue.Queue(maxsize=1024)
        self._closed = threading.Event()

    @property
    def task_id(self) -> str:
        return self.identity.task_id

    @property
    def workflow_run_id(self) -> str:
        return self.identity.workflow_run_id

    def events(self) -> Iterator[ExecutionEvent]:
        while True:
            try:
                event = self._queue.get(timeout=0.1)
            except queue.Empty:
                if self._closed.is_set():
                    return
                continue
            if event is self._sentinel:
                return
            yield event

    def publish(self, event: ExecutionEvent) -> None:
        while not self._closed.is_set():
            try:
                self._queue.put(event, timeout=0.1)
                return
            except queue.Full:
                continue

    def finish(self) -> None:
        while not self._closed.is_set():
            try:
                self._queue.put(self._sentinel, timeout=0.1)
                return
            except queue.Full:
                continue

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._queue.put_nowait(self._sentinel)


class RunService:
    def __init__(
        self,
        store: Store,
        engine: Engine | None = None,
        *,
        backend: CeleryRunBackend | None = None,
    ) -> None:
        self._store = store
        self._engine = engine
        self._backend = backend
        self._active: dict[str, EngineExecution] = {}
        self._consumers: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._logger = logging.getLogger(__name__)

    def start(
        self,
        app_id: AppId,
        inputs: dict[str, Any],
        actor: Actor = LOCAL_ACTOR,
        *,
        root_node_id: str | None = None,
    ) -> RunSession:
        draft = self._store.get_draft(app_id)
        if draft is None:
            raise DraftNotFound
        return self._start_snapshot(
            app_id=app_id,
            workflow_id=draft.workflow_id,
            graph=draft.graph,
            draft_hash=draft.hash,
            inputs=inputs,
            actor=actor,
            root_node_id=root_node_id,
        )

    def start_published(
        self,
        app_id: AppId,
        inputs: dict[str, Any],
        actor: Actor,
        *,
        call_depth: int = 0,
        call_stack: tuple[str, ...] = (),
        root_node_id: str | None = None,
    ) -> RunSession:
        published = self._store.get_published(app_id)
        if published is None:
            raise DraftNotFound
        return self._start_snapshot(
            app_id=app_id,
            workflow_id=published.id,
            graph=published.graph,
            draft_hash=published.hash,
            inputs=inputs,
            actor=actor,
            call_depth=call_depth,
            call_stack=call_stack,
            root_node_id=root_node_id,
        )

    def _start_snapshot(
        self,
        *,
        app_id: AppId,
        workflow_id: str,
        graph: GraphDocument,
        draft_hash: DraftHash,
        inputs: dict[str, Any],
        actor: Actor,
        call_depth: int = 0,
        call_stack: tuple[str, ...] = (),
        root_node_id: str | None = None,
    ) -> RunSession:
        identity = RunIdentity(
            app_id=app_id,
            workflow_id=workflow_id,
            workflow_run_id=str(uuid7()),
            task_id=str(uuid4()),
        )
        snapshot = RunSnapshot(
            identity=identity,
            graph=graph,
            draft_hash=draft_hash,
            inputs=inputs,
            actor=actor,
            created_at=datetime.now(UTC),
            call_depth=call_depth,
            call_stack=call_stack,
            root_node_id=root_node_id,
        )
        if self._backend is not None:
            self._store.create_run(snapshot)
            return self._backend.start(snapshot)
        if self._engine is None:
            raise RuntimeError("Local execution engine is not configured")
        try:
            execution = self._engine.start(snapshot)
        except ValueError as error:
            raise InvalidWorkflow(str(error)) from error
        session = RunSession(identity)
        self._store.create_run(snapshot)
        thread = threading.Thread(
            target=self._consume,
            args=(execution, session),
            name=f"workflow-{identity.task_id}",
            daemon=True,
        )
        with self._lock:
            self._consumers = {
                task_id: consumer for task_id, consumer in self._consumers.items() if consumer.is_alive()
            }
            self._active[identity.task_id] = execution
            self._consumers[identity.task_id] = thread
        thread.start()
        return session

    def stop(self, app_id: AppId, task_id: str) -> None:
        identity = self._store.get_run_identity(app_id, task_id)
        if identity is None:
            raise RunNotFound
        if self._backend is not None:
            self._backend.stop(identity)
            return
        with self._lock:
            execution = self._active.get(task_id)
        if execution is not None:
            execution.request_stop("Stopped by user")

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
            return
        with self._lock:
            executions = tuple(self._active.values())
            consumers = tuple(self._consumers.values())
        for execution in executions:
            execution.request_stop("Application shutting down")
        for consumer in consumers:
            consumer.join()
        with self._lock:
            self._consumers = {
                task_id: consumer for task_id, consumer in self._consumers.items() if consumer.is_alive()
            }

    def _consume(self, execution: EngineExecution, session: RunSession) -> None:
        terminal_projected = False
        next_sequence = 0
        try:
            for event in execution.events():
                next_sequence = event.sequence + 1
                self._store.project_event(event)
                if isinstance(event, WorkflowFinished):
                    terminal_projected = True
                session.publish(event)
        except Exception as error:
            self._logger.exception("Workflow run coordination failed")
            if not terminal_projected:
                now = datetime.now(UTC)
                failure = WorkflowFinished(
                    identity=session.identity,
                    sequence=next_sequence,
                    outcome=RunFailed(outputs={}, error=str(error), exceptions_count=1),
                    elapsed_time=0.0,
                    total_tokens=0,
                    total_steps=0,
                    created_at=now,
                    finished_at=now,
                )
                try:
                    self._store.project_event(failure)
                except TerminalStateConflict:
                    pass
                else:
                    session.publish(failure)
        finally:
            with self._lock:
                self._active.pop(session.task_id, None)
            session.finish()
