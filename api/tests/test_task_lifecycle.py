import asyncio
import os

import pytest
import redis

from api.models.engine import create_session_factory

DATABASE_URL = os.environ.get("TEST_POSTGRESQL_URL")
REDIS_URL = os.environ.get("TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(
    DATABASE_URL is None or REDIS_URL is None,
    reason="PostgreSQL and Redis integration URLs are not configured",
)


def test_celery_schedule_task_can_run_consecutively(monkeypatch) -> None:
    from api import tasks

    engine, session_factory = create_session_factory(DATABASE_URL)
    monkeypatch.setattr(tasks, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(tasks, "async_engine", engine)
    monkeypatch.setattr(tasks, "create_sync_client", lambda: redis.Redis.from_url(REDIS_URL))
    try:
        assert tasks.dispatch_schedules.run() == 0
        assert tasks.dispatch_schedules.run() == 0
    finally:
        asyncio.run(engine.dispose())
