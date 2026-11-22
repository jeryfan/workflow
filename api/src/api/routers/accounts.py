from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies.auth import require_principal
from api.dependencies.db import get_db
from api.models.account import Account
from api.schemas.auth import Principal
from api.services.authorization import AuthorizationService

router = APIRouter()


@router.get("/account/profile")
async def profile(
    principal: Principal = Depends(require_principal),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    account = await db.get(Account, principal.account_id)
    if account is None:
        raise RuntimeError("Authenticated account no longer exists")
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
async def permissions(
    principal: Principal = Depends(require_principal),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    roles = AuthorizationService(db).roles
    role = principal.role.value
    return {
        "account": {"role": role, "permission_keys": sorted(roles.account_keys(role))},
        "app": {"default_permission_keys": sorted(roles.app_keys(role))},
    }


@router.get("/accounts")
async def accounts(
    _principal: Principal = Depends(require_principal),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    rows = (
        (await db.execute(select(Account).where(Account.status == "active").order_by(Account.created_at)))
        .scalars()
        .all()
    )
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
