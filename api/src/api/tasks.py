from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from datetime import UTC, datetime

from api.app import celery
from api.extensions.ext_redis import create_sync_client
from api.models.engine import AsyncSessionLocal, async_engine
from api.models.entities import RunFailed, RunSnapshot, TerminalStateConflict, WorkflowFinished
from api.services.distributed_runs import CeleryRunBackend, run_channel, stop_channel
from api.services.engine import GraphonWorkflowEngine
from api.services.files import FileService
from api.services.run_messages import serialize_event
from api.services.run_service import LocalRunCoordinator, RunService
from api.services.runs import RunHistoryService
from api.services.triggers import TriggerScheduler

logger = logging.getLogger(__name__)


@celery.task(name="workflow.execute")
def execute_workflow(run_id: str) -> None:
    asyncio.run(_execute_workflow(run_id))


async def _execute_workflow(run_id: str) -> None:
    client = create_sync_client()
    snapshot: RunSnapshot | None = None
    stop_listener = None
    pubsub = None
    terminal_projected = False
    async with AsyncSessionLocal() as db:
        history = RunHistoryService(db)
        try:
            snapshot = await history.snapshot(run_id)
            if snapshot is None:
                raise ValueError("Workflow run snapshot not found")
            prepared = await GraphonWorkflowEngine.prepare_inputs(snapshot, FileService(db))
            coordinator = LocalRunCoordinator()
            runs = RunService(db, session_factory=AsyncSessionLocal, coordinator=coordinator)
            if runs.engine is None:
                raise RuntimeError("Workflow execution engine is not configured")
            execution = runs.engine.start(prepared)
            pubsub = client.pubsub(ignore_subscribe_messages=True)
            pubsub.subscribe(stop_channel(snapshot.identity.task_id))

            def listen_for_stop() -> None:
                try:
                    for _message in pubsub.listen():
                        execution.request_stop("Stopped by user")
                        return
                except (OSError, ValueError):
                    return

            stop_listener = threading.Thread(target=listen_for_stop, daemon=True)
            stop_listener.start()
            for event in execution.events():
                await history.project(event)
                terminal_projected = terminal_projected or isinstance(event, WorkflowFinished)
                await asyncio.to_thread(
                    client.publish,
                    run_channel(snapshot.identity.task_id),
                    serialize_event(event),
                )
        except Exception as error:
            logger.exception("Distributed workflow execution failed")
            if snapshot is not None and not terminal_projected:
                now = datetime.now(UTC)
                failure = WorkflowFinished(
                    identity=snapshot.identity,
                    sequence=0,
                    outcome=RunFailed(outputs={}, error=str(error), exceptions_count=1),
                    elapsed_time=0.0,
                    total_tokens=0,
                    total_steps=0,
                    created_at=now,
                    finished_at=now,
                )
                with contextlib.suppress(TerminalStateConflict):
                    await history.project(failure)
                await asyncio.to_thread(
                    client.publish,
                    run_channel(snapshot.identity.task_id),
                    serialize_event(failure),
                )
        finally:
            if pubsub is not None:
                pubsub.close()
            if stop_listener is not None:
                stop_listener.join(timeout=1)
            client.close()
            await async_engine.dispose()


@celery.task(name="workflow.dispatch_schedules")
def dispatch_schedules() -> int:
    return asyncio.run(_dispatch_schedules())


async def _dispatch_schedules() -> int:
    client = create_sync_client()
    try:
        coordinator = LocalRunCoordinator()
        scheduler = TriggerScheduler(
            AsyncSessionLocal,
            lambda session: RunService(
                session,
                backend=CeleryRunBackend(celery, client, RunHistoryService(session)),
                session_factory=AsyncSessionLocal,
                coordinator=coordinator,
            ),
        )
        return await scheduler.poll_once()
    finally:
        client.close()
        await async_engine.dispose()
