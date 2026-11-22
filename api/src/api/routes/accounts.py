from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select

from api.dependencies.auth import require_principal

router = APIRouter()


@router.get("/account/profile")
def profile(request: Request) -> dict[str, Any]:
    principal = require_principal(request)
    store = request.app.state.store
    with store.engine.connect() as connection:
        account = connection.execute(store.accounts.select().where(store.accounts.c.id == principal.account_id)).one()
    return {
        "id": account.id,
        "name": account.name,
        "email": account.email,
        "avatar": account.avatar_url,
        "avatar_url": account.avatar_url,
        "interface_language": account.interface_language,
        "interface_theme": account.interface_theme,
        "timezone": account.timezone,
        "role": account.role,
        "is_password_set": True,
        "last_login_at": account.last_login_at,
        "last_login_ip": account.last_login_ip,
        "created_at": account.created_at,
    }


@router.get("/account/permissions")
def permissions(request: Request) -> dict[str, Any]:
    principal = require_principal(request)
    roles = request.app.state.authorization.roles
    role = principal.role.value
    return {
        "account": {"role": role, "permission_keys": sorted(roles.account_keys(role))},
        "app": {"default_permission_keys": sorted(roles.app_keys(role))},
    }


@router.get("/accounts")
def accounts(request: Request) -> dict[str, Any]:
    require_principal(request)
    store = request.app.state.store
    with store.engine.connect() as connection:
        rows = connection.execute(
            select(store.accounts).where(store.accounts.c.status == "active").order_by(store.accounts.c.created_at)
        ).all()
    return {
        "accounts": [
            {
                "id": account.id,
                "name": account.name,
                "email": account.email,
                "avatar": account.avatar_url,
                "avatar_url": account.avatar_url,
                "role": account.role,
                "status": account.status,
                "created_at": account.created_at,
                "last_login_at": account.last_login_at,
            }
            for account in rows
        ]
    }
