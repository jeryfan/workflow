import json
import os
import threading
from pathlib import Path

import pytest
import redis
from celery import Celery

from api.config import get_settings
from api.db.store import Store
from api.main import DEFAULT_APP_ID
from api.models.entities import RunIdentity, RunSnapshot, WorkflowFinished
from api.services.auth import AccountRole, Principal
from api.services.distributed_runs import CeleryRunBackend, stop_channel
from api.services.dsl import DslService
from api.services.workflows import LOCAL_ACTOR, RunService, WorkflowService

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text())
REDIS_URL = os.environ.get("TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(REDIS_URL is None, reason="Redis integration URL is not configured")


def snapshot(store: Store) -> RunSnapshot:
    from datetime import UTC, datetime

    return RunSnapshot(
        identity=RunIdentity(
            app_id=DEFAULT_APP_ID,
            workflow_id="workflow-1",
            workflow_run_id="run-1",
            task_id="task-1",
        ),
        graph=store.get_draft(DEFAULT_APP_ID).graph,
        draft_hash=store.get_draft(DEFAULT_APP_ID).hash,
        inputs=FIXTURE["inputs"],
        actor=LOCAL_ACTOR,
        created_at=datetime.now(UTC),
    )


def prepared_store(tmp_path: Path) -> Store:
    from api.models.entities import GraphDocument, SaveDraft

    store = Store(f"sqlite:///{tmp_path / 'workflow.db'}")
    store.ensure_app(DEFAULT_APP_ID)
    store.save_draft(
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
    return store


def test_celery_dispatch_and_redis_stop_channel(tmp_path: Path) -> None:
    store = prepared_store(tmp_path)
    client = redis.Redis.from_url(REDIS_URL)
    client.flushdb()
    celery = Celery("test", broker=REDIS_URL)
    backend = CeleryRunBackend(store, celery, client)
    session = backend.start(snapshot(store))
    assert client.llen("celery") == 1

    pubsub = client.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(stop_channel("task-1"))
    backend.stop(session.identity)
    message = None
    for _ in range(50):
        message = pubsub.get_message(timeout=0.02)
        if message is not None:
            break
    assert message is not None
    assert message["data"] == b"stop"
    pubsub.close()
    session.close()
    backend.close()
    store.close()


def test_worker_executes_snapshot_and_streams_real_redis_events(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'workflow.db'}")
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    get_settings.cache_clear()
    from api.tasks import execute_workflow

    store = prepared_store(tmp_path)
    client = redis.Redis.from_url(REDIS_URL)
    client.flushdb()
    backend = CeleryRunBackend(store, Celery("test", broker=REDIS_URL), client)
    runs = RunService(store, backend=backend)
    session = runs.start(DEFAULT_APP_ID, FIXTURE["inputs"], LOCAL_ACTOR)

    worker = threading.Thread(target=execute_workflow.run, args=[session.identity.workflow_run_id])
    worker.start()
    events = list(session.events())
    worker.join(10)

    assert not worker.is_alive()
    assert events[-1].identity == session.identity
    assert isinstance(events[-1], WorkflowFinished)
    assert events[-1].outcome.outputs == FIXTURE["expected_outputs"]
    detail = store.get_run_record(DEFAULT_APP_ID, session.identity.workflow_run_id)
    assert detail.status == "succeeded"
    assert detail.outputs == FIXTURE["expected_outputs"]
    backend.close()
    store.close()
    get_settings.cache_clear()


def test_pending_dsl_import_is_shared_between_service_instances(tmp_path: Path) -> None:
    import yaml

    client = redis.Redis.from_url(REDIS_URL)
    client.flushdb()
    store = prepared_store(tmp_path)
    workflows = WorkflowService(store, ROOT / "contracts" / "nodes.json")
    first = DslService(workflows, redis_client=client)
    second = DslService(workflows, redis_client=client)
    principal = Principal(
        account_id="owner-1",
        email="owner@example.com",
        name="Owner",
        role=AccountRole.OWNER,
        session_id="session-1",
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

    staged = first.stage_or_import(
        app_id=DEFAULT_APP_ID,
        content=yaml.safe_dump(document),
        name=None,
        principal=principal,
    )
    assert staged["status"] == "pending"
    pending = second.pending_import(staged["id"], principal)
    assert pending is not None
    confirmed = second.confirm(staged["id"], principal)
    assert confirmed["status"] == "completed"
    assert second.pending_import(staged["id"], principal) is None
    client.close()
    store.close()
