from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import cast

from celery import Celery
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from api.configs import app_config
from api.constants import CONSOLE_API_PREFIX, DEFAULT_APP_ID
from api.exceptions.handlers import init_exception_handlers
from api.models.base import Base
from api.models.engine import create_session_factory
from api.services.run_service import LocalRunCoordinator


def initialize_extensions(app: FastAPI) -> None:
    from api.extensions import ext_celery, ext_logging, ext_redis, ext_storage

    for extension in (ext_logging, ext_redis, ext_celery, ext_storage):
        extension.init_app(app)


def init_router(app: FastAPI) -> None:
    from api.routers import accounts, apps, auth, runs, system, tools, triggers, workflows
    from api.routers.files import management_router
    from api.routers.files import public_router as file_public_router
    from api.routers.public import router as public_router
    from api.routers.triggers import public_router as trigger_public_router

    for router in (
        auth.router,
        accounts.router,
        system.router,
        apps.router,
        workflows.router,
        runs.router,
        tools.router,
        triggers.management_router,
        management_router,
    ):
        app.include_router(router, prefix=CONSOLE_API_PREFIX)
    app.include_router(public_router, prefix="/api")
    app.include_router(file_public_router, prefix="/api")
    app.include_router(trigger_public_router)


def init_middlewares(app: FastAPI) -> None:
    from api.middlewares.csrf import CsrfMiddleware
    from api.middlewares.trace import TraceMiddleware

    app.add_middleware(CsrfMiddleware)
    origins = list(dict.fromkeys([*app_config.CORS_ALLOWED_ORIGINS, app_config.PUBLIC_WEB_URL]))
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=app_config.CORS_ALLOW_CREDENTIALS,
            allow_methods=app_config.CORS_ALLOWED_METHODS,
            allow_headers=app_config.CORS_ALLOWED_HEADERS,
        )
    if app_config.ALLOWED_HOSTS != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=app_config.ALLOWED_HOSTS)
    app.add_middleware(TraceMiddleware)


def create_app(database_url: str | None = None) -> FastAPI:
    engine, session_factory = create_session_factory(database_url)
    app = FastAPI(
        title="Workflow",
        docs_url="/api/docs" if app_config.DEBUG else None,
        redoc_url="/api/redoc" if app_config.DEBUG else None,
        openapi_url="/api/openapi.json" if app_config.DEBUG else None,
    )
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.run_coordinator = LocalRunCoordinator()
    schema_ready = False
    if database_url and database_url.startswith("sqlite"):

        async def create_schema() -> None:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

        asyncio.run(create_schema())
        schema_ready = True

    initialize_extensions(app)
    from api.extensions.ext_redis import create_sync_client, redis_client

    app.state.run_redis = create_sync_client()
    app.state.redis = redis_client
    init_exception_handlers(app)
    init_middlewares(app)
    init_router(app)
    register_domain_exception_handlers(app)

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from api.models.app import App
        from api.services.runs import RunHistoryService
        from api.services.triggers import TriggerScheduler

        if database_url and database_url.startswith("sqlite") and not schema_ready:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as db:
            if await db.get(App, DEFAULT_APP_ID) is None:
                now = int(time.time())
                db.add(
                    App(
                        id=DEFAULT_APP_ID,
                        name="Workflow",
                        mode="workflow",
                        description="",
                        use_icon_as_answer_icon=0,
                        created_at=now,
                        updated_at=now,
                    )
                )
                try:
                    await db.commit()
                except IntegrityError:
                    # Another server worker may have created the built-in app concurrently.
                    await db.rollback()
            if app_config.EXECUTION_BACKEND == "local":
                await RunHistoryService(db).fail_interrupted("Process interrupted")

        scheduler = None
        if app_config.EXECUTION_BACKEND == "local":
            from api.services.run_service import RunService

            scheduler = TriggerScheduler(
                session_factory,
                lambda db: RunService(
                    db,
                    session_factory=session_factory,
                    coordinator=app.state.run_coordinator,
                ),
            )
            scheduler.start()
        try:
            yield
        finally:
            if scheduler is not None:
                await scheduler.close()
            async with session_factory() as db:
                await RunService(
                    db,
                    session_factory=session_factory,
                    coordinator=app.state.run_coordinator,
                ).close()
            await engine.dispose()
            await redis_client.aclose()
            app.state.run_redis.close()

    app.router.lifespan_context = lifespan
    return app


def register_domain_exception_handlers(app: FastAPI) -> None:
    from api.routers.public import SiteUnavailable
    from api.services.authorization import AccessDenied, AppNotFound

    @app.exception_handler(SiteUnavailable)
    async def site_unavailable(_request: Request, _error: SiteUnavailable) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"code": "site_not_found", "message": "Workflow site is unavailable", "status": 404},
        )

    @app.exception_handler(AppNotFound)
    async def app_not_found(_request: Request, _error: AppNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"code": "app_not_found", "message": "App not found", "status": 404},
        )

    @app.exception_handler(AccessDenied)
    async def access_denied(_request: Request, error: AccessDenied) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={"code": "permission_denied", "message": f"Permission required: {error}", "status": 403},
        )


app = create_app()
celery = cast(Celery, app.state.celery)
