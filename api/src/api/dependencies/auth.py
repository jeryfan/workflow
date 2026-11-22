from __future__ import annotations

from fastapi import Request

from api.security.cookies import cookie_policy
from api.services.auth import AuthService, Principal


def require_principal(request: Request) -> Principal:
    auth: AuthService = request.app.state.auth
    cookies = cookie_policy(request)
    return auth.authenticate_request(
        cookies.access_token(request),
        cookies.csrf_token(request),
        request.headers.get("X-CSRF-Token"),
    )
