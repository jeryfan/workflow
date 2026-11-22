from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.configs import app_config


def create_session_factory(database_url: str | None = None):
    url = database_url or app_config.SQLALCHEMY_DATABASE_URI
    if url.startswith("sqlite:///"):
        url = url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    options = (
        {"connect_args": {"check_same_thread": False}}
        if url.startswith("sqlite")
        else app_config.SQLALCHEMY_ENGINE_OPTIONS
    )
    engine = create_async_engine(url, **options)
    factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return engine, factory


async_engine, AsyncSessionLocal = create_session_factory()
