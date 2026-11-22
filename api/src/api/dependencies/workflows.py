from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.configs import app_config
from api.models.entities import Actor
from api.schemas.auth import Principal
from api.services.apps import AppService
from api.services.authorization import AuthorizationService
from api.services.distributed_runs import CeleryRunBackend
from api.services.dsl import DslService
from api.services.files import FileService
from api.services.run_service import RunService
from api.services.runs import RunHistoryService
from api.services.workflows import WorkflowService

from .auth import require_principal
from .db import get_db


def get_app_service(request: Request, db: AsyncSession = Depends(get_db)) -> AppService:
    return AppService(db, AuthorizationService(db), FileService(db))


def get_workflow_service(db: AsyncSession = Depends(get_db)) -> WorkflowService:
    return WorkflowService(db)


def get_dsl_service(request: Request, db: AsyncSession = Depends(get_db)) -> DslService:
    return DslService(WorkflowService(db), redis_client=request.app.state.run_redis)


def get_run_service(request: Request, db: AsyncSession = Depends(get_db)) -> RunService:
    backend = None
    if app_config.EXECUTION_BACKEND == "celery":
        backend = CeleryRunBackend(
            request.app.state.celery,
            request.app.state.run_redis,
            RunHistoryService(db),
        )
    return RunService(
        db,
        backend=backend,
        session_factory=request.app.state.session_factory,
        coordinator=request.app.state.run_coordinator,
    )


async def authorized_actor(
    request: Request,
    app_id: str,
    permission: str,
    db: AsyncSession = Depends(get_db),
) -> Actor:
    principal = await require_principal(request, db)
    await AuthorizationService(db).require(principal, app_id, permission)
    return principal.actor


async def authenticated_actor(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Actor:
    principal: Principal = await require_principal(request, db)
    return principal.actor
