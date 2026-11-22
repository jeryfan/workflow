from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api.models.entities import (
    Actor,
    AppId,
    DraftHash,
    ExecutionEvent,
    GraphDocument,
    RunFailed,
    RunIdentity,
    RunSnapshot,
    TerminalStateConflict,
    WorkflowFinished,
)

from .files import FileService
from .runs import RunHistoryService

LOCAL_ACTOR = Actor(id="local-user", name="Local User", email="local@example.invalid")


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


@dataclass
class LocalRunCoordinator:
    lock: threading.Lock = field(default_factory=threading.Lock)
    active: dict[str, EngineExecution] = field(default_factory=dict)
    consumers: dict[str, threading.Thread] = field(default_factory=dict)


class RunService:
    def __init__(
        self,
        db: AsyncSession,
        engine: Engine | None = None,
        *,
        backend: Any = None,
        session_factory: Any = None,
        coordinator: LocalRunCoordinator | None = None,
    ) -> None:
        self.db = db
        self.history = RunHistoryService(db)
        self._backend = backend
        self._session_factory = session_factory
        self._coordinator = coordinator or LocalRunCoordinator()
        self._logger = logging.getLogger(__name__)
        if engine is None and backend is None:
            from .engine import GraphonWorkflowEngine
            from .tools import WorkflowToolService

            engine = GraphonWorkflowEngine(tool_service=WorkflowToolService(db, self))
        self._engine = engine

    @property
    def session_factory(self):
        return self._session_factory

    def isolated_session_factory(self):
        """Return a thread-local engine for non-SQLite execution and its session factory."""
        if self._session_factory is None:
            raise RuntimeError("Local execution requires a session factory")
        bind = self.db.bind
        if bind.dialect.name == "sqlite":
            return None, self._session_factory
        from api.models.engine import create_session_factory

        database_url = bind.url.render_as_string(hide_password=False)
        engine, factory = create_session_factory(database_url)
        return engine, factory

    @property
    def engine(self) -> Engine | None:
        return self._engine

    @property
    def coordinator(self) -> LocalRunCoordinator:
        return self._coordinator

    async def start_draft(
        self,
        app_id: AppId,
        inputs: dict[str, Any],
        actor: Actor = LOCAL_ACTOR,
        *,
        root_node_id: str | None = None,
    ) -> RunSession:
        from .workflows import WorkflowService

        draft = await WorkflowService(self.db).get_draft(app_id)
        return await self._start_snapshot(
            app_id=app_id,
            workflow_id=draft.workflow_id,
            graph=draft.graph,
            draft_hash=draft.hash,
            inputs=inputs,
            actor=actor,
            root_node_id=root_node_id,
        )

    async def start_published(
        self,
        app_id: AppId,
        inputs: dict[str, Any],
        actor: Actor,
        *,
        call_depth: int = 0,
        call_stack: tuple[str, ...] = (),
        root_node_id: str | None = None,
    ) -> RunSession:
        from .workflows import DraftNotFound, WorkflowService

        published = await WorkflowService(self.db).get_published(app_id)
        if published is None:
            raise DraftNotFound
        return await self._start_snapshot(
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

    async def _start_snapshot(
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
        from .engine import GraphonWorkflowEngine
        from .workflows import InvalidWorkflow

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
            await self.history.create(snapshot)
            return await self._backend.start(snapshot)
        if self._engine is None:
            raise RuntimeError("Local execution engine is not configured")
        if self._session_factory is None:
            raise RuntimeError("Local execution requires a session factory")
        prepared = await GraphonWorkflowEngine.prepare_inputs(snapshot, FileService(self.db))
        try:
            execution = await asyncio.to_thread(self._engine.start, prepared)
        except ValueError as error:
            raise InvalidWorkflow(str(error)) from error
        session = RunSession(identity)
        await self.history.create(snapshot)
        thread = threading.Thread(
            target=self._consume,
            args=(execution, session),
            name=f"workflow-{identity.task_id}",
            daemon=True,
        )
        coordinator = self._coordinator
        with coordinator.lock:
            coordinator.consumers = {
                task_id: consumer for task_id, consumer in coordinator.consumers.items() if consumer.is_alive()
            }
            coordinator.active[identity.task_id] = execution
            coordinator.consumers[identity.task_id] = thread
        thread.start()
        return session

    async def stop(self, app_id: AppId, task_id: str) -> None:
        identity = await self.history.identity(app_id, task_id)
        if identity is None:
            raise RunNotFound
        if self._backend is not None:
            await self._backend.stop(identity)
            return
        with self._coordinator.lock:
            execution = self._coordinator.active.get(task_id)
        if execution is not None:
            execution.request_stop("Stopped by user")

    async def close(self) -> None:
        if self._backend is not None:
            await asyncio.to_thread(self._backend.close)
            return
        coordinator = self._coordinator
        with coordinator.lock:
            executions = tuple(coordinator.active.values())
            consumers = tuple(coordinator.consumers.values())
        for execution in executions:
            execution.request_stop("Application shutting down")
        for consumer in consumers:
            await asyncio.to_thread(consumer.join)
        with coordinator.lock:
            coordinator.consumers = {
                task_id: consumer for task_id, consumer in coordinator.consumers.items() if consumer.is_alive()
            }

    def _consume(self, execution: EngineExecution, session: RunSession) -> None:
        engine, session_factory = self.isolated_session_factory()

        async def consume() -> None:
            try:
                await self._consume_async(execution, session, session_factory=session_factory)
            finally:
                if engine is not None:
                    await engine.dispose()

        asyncio.run(consume())

    async def _consume_async(
        self,
        execution: EngineExecution,
        session: RunSession,
        *,
        session_factory=None,
    ) -> None:
        terminal_projected = False
        next_sequence = 0
        factory = session_factory or self._session_factory
        async with factory() as db:
            history = RunHistoryService(db)
            try:
                for event in execution.events():
                    next_sequence = event.sequence + 1
                    await history.project(event)
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
                        await history.project(failure)
                    except TerminalStateConflict:
                        pass
                    else:
                        session.publish(failure)
            finally:
                with self._coordinator.lock:
                    self._coordinator.active.pop(session.task_id, None)
                session.finish()
