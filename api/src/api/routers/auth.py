from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies.auth import require_principal
from api.dependencies.db import get_db
from api.libs.cookie import (
    clear_auth_cookies,
    get_access_token,
    get_refresh_token,
    set_auth_cookies,
)
from api.libs.token import revoke_session_tokens
from api.schemas.auth import LoginPayload, Principal, SetupCommand, SetupPayload
from api.services.auth import AuthService, LoginCommand

router = APIRouter()


def decode_login_password(value: str) -> str | None:
    if len(value) > 8192:
        return None
    try:
        return base64.b64decode(value, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


@router.get("/setup")
async def get_setup(request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    setup_at = await AuthService(db).setup_at()
    content = {"step": "not_started"} if setup_at is None else {"step": "finished", "setup_at": setup_at}
    return JSONResponse(content, headers={"Cache-Control": "no-store"})


@router.post("/setup")
async def setup(payload: SetupPayload, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    await AuthService(db).setup(SetupCommand(**payload.model_dump()))
    return JSONResponse(
        status_code=201,
        content={"result": "success"},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/login")
async def login(payload: LoginPayload, request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    auth = AuthService(db)
    client_ip = request.client.host if request.client is not None else "unknown"
    password = decode_login_password(payload.password)
    if password is None:
        await auth.reject_login(payload.email, client_ip)
    issued = await auth.login(LoginCommand(email=payload.email, password=password, client_ip=client_ip))
    response = JSONResponse({"result": "success"}, headers={"Cache-Control": "no-store"})
    set_auth_cookies(
        response,
        request,
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        csrf_token=issued.csrf_token,
    )
    return response


@router.post("/refresh-token")
async def refresh_token(request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    issued = await AuthService(db).refresh(get_refresh_token(request))
    response = JSONResponse({"result": "success"}, headers={"Cache-Control": "no-store"})
    set_auth_cookies(
        response,
        request,
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        csrf_token=issued.csrf_token,
    )
    return response


@router.post("/logout")
async def logout(
    request: Request,
    _principal: Principal = Depends(require_principal),
) -> JSONResponse:
    await revoke_session_tokens(get_refresh_token(request), get_access_token(request))
    response = JSONResponse({"result": "success"}, headers={"Cache-Control": "no-store"})
    clear_auth_cookies(response, request)
    return response


@router.get("/init")
async def get_init() -> JSONResponse:
    return JSONResponse({"status": "finished"}, headers={"Cache-Control": "no-store"})
