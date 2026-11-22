from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

import redis

from api.celery_app import celery_app
from api.config import get_settings
from api.db.store import Store
from api.models.entities import RunFailed, TerminalStateConflict, WorkflowFinished
from api.services.distributed_runs import CeleryRunBackend, run_channel, stop_channel
from api.services.engine import GraphonWorkflowEngine
from api.services.run_messages import serialize_event
from api.services.runtime import create_file_service
from api.services.tools import WorkflowToolService
from api.services.triggers import TriggerScheduler
from api.services.workflows import RunService

logger = logging.getLogger(__name__)


def worker_services() -> tuple[Store, GraphonWorkflowEngine, redis.Redis]:
    settings = get_settings()
    store = Store(settings.database_url)
    files = create_file_service(store)
    engine = GraphonWorkflowEngine()
    engine.file_service = files
    runs = RunService(store, engine)
    engine.tool_service = WorkflowToolService(store, runs)
    client = redis.Redis.from_url(settings.redis_url, decode_responses=False)
    return store, engine, client


@celery_app.task(name="workflow.execute")
def execute_workflow(run_id: str) -> None:
    store, engine, client = worker_services()
    snapshot = None
    stop_listener = None
    pubsub = None
    terminal_projected = False
    try:
        snapshot = store.get_run_snapshot(run_id)
        if snapshot is None:
            raise ValueError("Workflow run snapshot not found")
        execution = engine.start(snapshot)
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(stop_channel(snapshot.identity.task_id))

        def listen_for_stop() -> None:
            try:
                for message in pubsub.listen():
                    if message is not None:
                        execution.request_stop("Stopped by user")
                        return
            except (OSError, ValueError, redis.ConnectionError):
                return

        stop_listener = threading.Thread(target=listen_for_stop, daemon=True)
        stop_listener.start()
        for event in execution.events():
            store.project_event(event)
            terminal_projected = terminal_projected or isinstance(event, WorkflowFinished)
            client.publish(run_channel(snapshot.identity.task_id), serialize_event(event))
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
            try:
                store.project_event(failure)
            except TerminalStateConflict:
                return
            client.publish(run_channel(snapshot.identity.task_id), serialize_event(failure))
    finally:
        if pubsub is not None:
            pubsub.close()
        if stop_listener is not None:
            stop_listener.join(timeout=1)
        client.close()
        store.close()


@celery_app.task(name="workflow.dispatch_schedules")
def dispatch_schedules() -> int:
    settings = get_settings()
    store = Store(settings.database_url)
    client = redis.Redis.from_url(settings.redis_url)
    try:
        engine = GraphonWorkflowEngine()
        files = create_file_service(store)
        engine.file_service = files
        local_runs = RunService(store, engine)
        engine.tool_service = WorkflowToolService(store, local_runs)
        backend = CeleryRunBackend(store, celery_app, client)
        runs = RunService(store, backend=backend)
        scheduler = TriggerScheduler(store, runs)
        return scheduler.poll_once()
    finally:
        client.close()
        store.close()
