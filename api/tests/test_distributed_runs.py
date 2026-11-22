import asyncio
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
import redis
from celery import Celery
from service_helpers import create_test_database

from api.app import DEFAULT_APP_ID
from api.models.entities import GraphDocument, RunIdentity, RunSnapshot, SaveDraft, WorkflowFinished
from api.schemas.auth import AccountRole, Principal
from api.services.distributed_runs import CeleryRunBackend, stop_channel
from api.services.dsl import DslService
from api.services.run_service import RunService
from api.services.runs import RunHistoryService
from api.services.workflows import LOCAL_ACTOR, WorkflowService

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text())
REDIS_URL = os.environ.get("TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(REDIS_URL is None, reason="Redis integration URL is not configured")


async def snapshot(db) -> RunSnapshot:
    draft = await WorkflowService(db).get_draft(DEFAULT_APP_ID)
    return RunSnapshot(
        identity=RunIdentity(
            app_id=DEFAULT_APP_ID,
            workflow_id="workflow-1",
            workflow_run_id="run-1",
            task_id="task-1",
        ),
        graph=draft.graph,
        draft_hash=draft.hash,
        inputs=FIXTURE["inputs"],
        actor=LOCAL_ACTOR,
        created_at=datetime.now(UTC),
    )


async def prepared_database(tmp_path: Path):
    engine, session_factory = await create_test_database(f"sqlite:///{tmp_path / 'workflow.db'}", (DEFAULT_APP_ID,))
    async with session_factory() as db:
        await WorkflowService(db).save_draft(
            SaveDraft(
                app_id=DEFAULT_APP_ID,
                graph=GraphDocument.parse(FIXTURE["graph"]),
                features={},
                conversation_variables=(),
                expected_hash=None,
                force=True,
                actor=LOCAL_ACTOR,
            )
        )
    return engine, session_factory


def test_celery_dispatch_and_redis_stop_channel(tmp_path: Path) -> None:
    async def exercise() -> None:
        engine, session_factory = await prepared_database(tmp_path)
        client = redis.Redis.from_url(REDIS_URL)
        client.flushdb()
        try:
            async with session_factory() as db:
                celery = Celery("test", broker=REDIS_URL)
                backend = CeleryRunBackend(celery, client, RunHistoryService(db))
                session = await backend.start(await snapshot(db))
                assert client.llen("celery") == 1

                pubsub = client.pubsub(ignore_subscribe_messages=True)
                pubsub.subscribe(stop_channel("task-1"))
                await backend.stop(session.identity)
                message = None
                for _ in range(50):
                    message = pubsub.get_message(timeout=0.02)
                    if message is not None:
                        break
                assert message is not None
                assert message["data"] == b"stop"
                pubsub.close()
                session.close()
        finally:
            client.close()
            await engine.dispose()

    asyncio.run(exercise())


def test_worker_executes_snapshot_and_streams_real_redis_events(tmp_path: Path, monkeypatch) -> None:
    async def exercise() -> None:
        engine, session_factory = await prepared_database(tmp_path)
        client = redis.Redis.from_url(REDIS_URL)
        client.flushdb()
        from api import tasks
        from api.tasks import execute_workflow

        monkeypatch.setattr(tasks, "AsyncSessionLocal", session_factory)
        monkeypatch.setattr(tasks, "create_sync_client", lambda: redis.Redis.from_url(REDIS_URL))
        worker = None
        try:
            async with session_factory() as db:
                backend = CeleryRunBackend(Celery("test", broker=REDIS_URL), client, RunHistoryService(db))
                runs = RunService(db, backend=backend, session_factory=session_factory)
                session = await runs.start_draft(DEFAULT_APP_ID, FIXTURE["inputs"], LOCAL_ACTOR)

                worker = threading.Thread(target=execute_workflow.run, args=[session.identity.workflow_run_id])
                worker.start()
                events = await asyncio.to_thread(lambda: list(session.events()))
                await asyncio.to_thread(worker.join, 10)

                assert not worker.is_alive()
                assert events[-1].identity == session.identity
                assert isinstance(events[-1], WorkflowFinished)
                assert events[-1].outcome.outputs == FIXTURE["expected_outputs"]
                detail = await RunHistoryService(db).get(DEFAULT_APP_ID, session.identity.workflow_run_id)
                assert detail is not None
                assert detail.status == "succeeded"
                assert detail.outputs == FIXTURE["expected_outputs"]
        finally:
            if worker is not None:
                worker.join(1)
            client.close()
            await engine.dispose()

    asyncio.run(exercise())


def test_pending_dsl_import_is_shared_between_service_instances(tmp_path: Path) -> None:
    import yaml

    async def exercise() -> None:
        client = redis.Redis.from_url(REDIS_URL)
        client.flushdb()
        engine, session_factory = await prepared_database(tmp_path)
        try:
            async with session_factory() as db:
                workflows = WorkflowService(db, ROOT / "contracts" / "nodes.json")
                first = DslService(workflows, redis_client=client)
                second = DslService(workflows, redis_client=client)
                principal = Principal(
                    account_id="owner-1",
                    email="owner@example.com",
                    name="Owner",
                    role=AccountRole.OWNER,
                )
                document = {
                    "version": "0.8.0",
                    "kind": "app",
                    "app": {"mode": "workflow", "name": "Pending import"},
                    "workflow": {
                        "graph": FIXTURE["graph"],
                        "features": {},
                        "environment_variables": [],
                        "conversation_variables": [],
                    },
                }

                staged = await first.stage_or_import(
                    app_id=DEFAULT_APP_ID,
                    content=yaml.safe_dump(document),
                    name=None,
                    principal=principal,
                )
                assert staged["status"] == "pending"
                pending = second.pending_import(staged["id"], principal)
                assert pending is not None
                confirmed = await second.confirm(staged["id"], principal)
                assert confirmed["status"] == "completed"
                assert second.pending_import(staged["id"], principal) is None
        finally:
            client.close()
            await engine.dispose()

    asyncio.run(exercise())
