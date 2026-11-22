from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.exceptions import AuthenticationFailedError
from api.libs.cookie import get_access_token
from api.schemas.auth import Principal
from api.services.auth import AuthService

from .db import get_db


async def require_principal(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Principal:
    token = get_access_token(request)
    if not token:
        raise AuthenticationFailedError("Authentication required")
    return await AuthService(db).authenticate_access(token)
