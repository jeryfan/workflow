from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.db.store import AlreadySetupError
from api.dependencies.auth import require_principal
from api.responses import error_response
from api.schemas.auth import LoginPayload, SetupPayload
from api.security.cookies import cookie_policy
from api.services.auth import (
    AuthenticationFailed,
    AuthService,
    InvalidSetupInput,
    LoginCommand,
    LoginThrottled,
    SetupCommand,
)

router = APIRouter()


def decode_login_password(value: str) -> str | None:
    if len(value) > 8192:
        return None
    try:
        return base64.b64decode(value, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


@router.get("/setup")
def get_setup(request: Request) -> JSONResponse:
    auth: AuthService = request.app.state.auth
    setup_at = auth.setup_at()
    content = {"step": "not_started"} if setup_at is None else {"step": "finished", "setup_at": setup_at}
    return JSONResponse(content, headers={"Cache-Control": "no-store"})


@router.post("/setup")
def setup(payload: SetupPayload, request: Request) -> JSONResponse:
    auth: AuthService = request.app.state.auth
    try:
        auth.setup(SetupCommand(**payload.model_dump()))
    except InvalidSetupInput as error:
        return error_response(400, "invalid_argument", str(error))
    except AlreadySetupError:
        return error_response(403, "already_setup", "Setup has already been completed")
    return JSONResponse(
        status_code=201,
        content={"result": "success"},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/login")
def login(payload: LoginPayload, request: Request) -> JSONResponse:
    auth: AuthService = request.app.state.auth
    client_ip = request.client.host if request.client is not None else "unknown"
    password = decode_login_password(payload.password)
    try:
        if password is None:
            auth.reject_login(payload.email, client_ip)
        issued = auth.login(LoginCommand(email=payload.email, password=password, client_ip=client_ip))
    except LoginThrottled:
        return error_response(429, "email_password_login_limit", "Too many login failures")
    except AuthenticationFailed:
        return error_response(401, "authentication_failed", "Invalid email or password")
    response = JSONResponse({"result": "success"}, headers={"Cache-Control": "no-store"})
    cookie_policy(request).set_session(response, issued)
    return response


@router.post("/refresh-token")
def refresh_token(request: Request) -> JSONResponse:
    auth: AuthService = request.app.state.auth
    cookies = cookie_policy(request)
    try:
        issued = auth.refresh(cookies.refresh_token(request))
    except AuthenticationFailed:
        return error_response(401, "authentication_failed", "Invalid refresh token")
    response = JSONResponse({"result": "success"}, headers={"Cache-Control": "no-store"})
    cookies.set_session(response, issued)
    return response


@router.post("/logout")
def logout(request: Request) -> JSONResponse:
    principal = require_principal(request)
    request.app.state.auth.logout(principal)
    response = JSONResponse({"result": "success"}, headers={"Cache-Control": "no-store"})
    cookie_policy(request).clear_session(response)
    return response


@router.get("/init")
def get_init() -> JSONResponse:
    return JSONResponse({"status": "finished"}, headers={"Cache-Control": "no-store"})
