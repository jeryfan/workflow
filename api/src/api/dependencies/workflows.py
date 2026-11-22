from __future__ import annotations

from fastapi import Request

from api.dependencies.auth import require_principal
from api.models.entities import Actor


def authorized_actor(request: Request, app_id: str, permission: str) -> Actor:
    principal = require_principal(request)
    request.app.state.authorization.require(principal, app_id, permission)
    return principal.actor


def authenticated_actor(request: Request) -> Actor:
    return require_principal(request).actor
