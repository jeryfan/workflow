from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.celery_app import celery_app
from api.config import PROJECT_ROOT, get_settings
from api.db.store import Store
from api.routes.accounts import router as accounts_router
from api.routes.apps import router as apps_router
from api.routes.auth import router as auth_router
from api.routes.files import management_router as file_management_router
from api.routes.files import public_router as file_public_router
from api.routes.public import SiteUnavailable
from api.routes.public import router as public_router
from api.routes.runs import router as runs_router
from api.routes.system import router as system_router
from api.routes.tools import router as tool_router
from api.routes.triggers import management_router as trigger_management_router
from api.routes.triggers import public_router as trigger_public_router
from api.routes.workflows import router as workflows_router
from api.services.apps import AppService
from api.services.auth import AuthenticationFailed, AuthService, InvalidCsrf
from api.services.authorization import AccessDenied, AppNotFound, AuthorizationService
from api.services.distributed_runs import CeleryRunBackend
from api.services.dsl import DslService
from api.services.engine import GraphonWorkflowEngine
from api.services.runtime import create_file_service
from api.services.tools import WorkflowToolService
from api.services.triggers import TriggerScheduler, TriggerService
from api.services.workflows import RunService, WorkflowService

DEFAULT_APP_ID = "00000000-0000-0000-0000-000000000001"


def create_app(database_url: str | None = None) -> FastAPI:
    settings = get_settings()
    database_url = database_url or settings.database_url
    if settings.app_env == "production":
        if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("Production requires PostgreSQL")
        if settings.execution_backend != "celery" or settings.storage_backend != "s3":
            raise ValueError("Production requires Celery and S3 storage")
        if settings.file_signing_key is None:
            raise ValueError("Production requires FILE_SIGNING_KEY")
    store = Store(database_url)
    store.ensure_app(DEFAULT_APP_ID)
    workflows = WorkflowService(store, PROJECT_ROOT / "contracts" / "nodes.json")
    engine = GraphonWorkflowEngine()
    files = create_file_service(store)
    engine.file_service = files
    redis_client = None
    if settings.execution_backend == "celery":
        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=5,
            health_check_interval=30,
        )
        redis_client = client
        backend = CeleryRunBackend(store, celery_app, client)
        runs = RunService(store, backend=backend)
    else:
        runs = RunService(store, engine)
    workflow_tools = WorkflowToolService(store, runs)
    engine.tool_service = workflow_tools
    triggers = TriggerService(store, runs)
    trigger_scheduler = TriggerScheduler(store, runs)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if settings.execution_backend == "local":
            store.fail_interrupted("Process interrupted")
            trigger_scheduler.start()
        try:
            yield
        finally:
            trigger_scheduler.close()
            runs.close()
            store.close()

    app = FastAPI(title="Workflow Engine API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(
            dict.fromkeys(
                origin
                for origin in [
                    "http://localhost:3000",
                    "http://127.0.0.1:3000",
                    settings.public_web_url,
                ]
                if origin
            )
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.store = store
    app.state.files = files
    app.state.auth = AuthService(store)
    app.state.authorization = AuthorizationService(store)
    app.state.apps = AppService(store, app.state.authorization, files)
    app.state.workflows = workflows
    app.state.dsl = DslService(workflows, redis_client=redis_client)
    app.state.runs = runs
    app.state.workflow_tools = workflow_tools
    app.state.triggers = triggers
    app.state.trigger_scheduler = trigger_scheduler
    app.include_router(auth_router, prefix="/console/api")
    app.include_router(accounts_router, prefix="/console/api")
    app.include_router(system_router, prefix="/console/api")
    app.include_router(apps_router, prefix="/console/api")
    app.include_router(workflows_router, prefix="/console/api")
    app.include_router(runs_router, prefix="/console/api")
    app.include_router(tool_router, prefix="/console/api")
    app.include_router(trigger_management_router, prefix="/console/api")
    app.include_router(file_management_router, prefix="/console/api")
    app.include_router(public_router, prefix="/api")
    app.include_router(file_public_router, prefix="/api")
    app.include_router(trigger_public_router)

    @app.exception_handler(SiteUnavailable)
    def site_unavailable(_request: Request, _error: SiteUnavailable) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"code": "site_not_found", "message": "Workflow site is unavailable", "status": 404},
        )

    @app.exception_handler(AppNotFound)
    def app_not_found(_request: Request, _error: AppNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"code": "app_not_found", "message": "App not found", "status": 404},
        )

    @app.exception_handler(AccessDenied)
    def access_denied(_request: Request, error: AccessDenied) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={"code": "permission_denied", "message": f"Permission required: {error}", "status": 403},
        )

    @app.exception_handler(AuthenticationFailed)
    def authentication_failed(_request: Request, _error: AuthenticationFailed) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={"code": "unauthorized", "message": "Authentication required", "status": 401},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(InvalidCsrf)
    def invalid_csrf(_request: Request, _error: InvalidCsrf) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={"code": "invalid_csrf", "message": "A valid CSRF token is required", "status": 403},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
