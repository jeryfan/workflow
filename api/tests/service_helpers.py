import time
from collections.abc import Iterable

from api.models import App, Base
from api.models.engine import create_session_factory


async def create_test_database(database_url: str, app_ids: Iterable[str] = ()):
    engine, session_factory = create_session_factory(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as db:
        now = int(time.time())
        for app_id in app_ids:
            db.add(App(id=app_id, name=app_id, mode="workflow", created_at=now, updated_at=now))
        await db.commit()
    return engine, session_factory


async def ensure_app(db, app_id: str, *, name: str | None = None) -> None:
    if await db.get(App, app_id) is None:
        now = int(time.time())
        db.add(App(id=app_id, name=name or app_id, mode="workflow", created_at=now, updated_at=now))
        await db.commit()
