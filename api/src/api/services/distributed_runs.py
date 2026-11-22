from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime

import redis
from celery import Celery

from api.models.entities import RunFailed, RunIdentity, RunSnapshot, WorkflowFinished, WorkflowPaused
from api.services.run_messages import deserialize_event, serialize_event
from api.services.run_service import RunSession
from api.services.runs import RunHistoryService


def run_channel(task_id: str) -> str:
    return f"workflow:runs:{task_id}:events"


def stop_channel(task_id: str) -> str:
    return f"workflow:runs:{task_id}:stop"


class RedisRunSession(RunSession):
    def __init__(self, client: redis.Redis, identity: RunIdentity) -> None:
        self.identity = identity
        self._client = client
        self._pubsub = client.pubsub(ignore_subscribe_messages=True)
        self._pubsub.subscribe(run_channel(identity.task_id))
        self._closed = threading.Event()

    def events(self):
        try:
            while not self._closed.is_set():
                message = self._pubsub.get_message(timeout=0.1)
                if message is None:
                    continue
                event = deserialize_event(message["data"])
                yield event
                if isinstance(event, WorkflowFinished | WorkflowPaused):
                    return
        finally:
            self.close()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._pubsub.close()


class CeleryRunBackend:
    def __init__(self, celery: Celery, client: redis.Redis, history: RunHistoryService) -> None:
        self.celery = celery
        self.client = client
        self.history = history

    async def start(self, snapshot: RunSnapshot) -> RedisRunSession:
        session = RedisRunSession(self.client, snapshot.identity)
        try:
            await asyncio.to_thread(
                self.celery.send_task,
                "workflow.execute",
                args=[snapshot.identity.workflow_run_id],
                task_id=snapshot.identity.task_id,
            )
        except Exception as error:
            now = datetime.now(UTC)
            failure = WorkflowFinished(
                identity=snapshot.identity,
                sequence=0,
                outcome=RunFailed(outputs={}, error=f"Unable to dispatch workflow: {error}", exceptions_count=1),
                elapsed_time=0.0,
                total_tokens=0,
                total_steps=0,
                created_at=now,
                finished_at=now,
            )
            await self.history.project(failure)
            await asyncio.to_thread(
                self.client.publish,
                run_channel(snapshot.identity.task_id),
                serialize_event(failure),
            )
        return session

    async def stop(self, identity: RunIdentity) -> None:
        await asyncio.to_thread(self.client.publish, stop_channel(identity.task_id), "stop")

    def close(self) -> None:
        self.client.close()
