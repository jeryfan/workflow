import asyncio
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from pydantic import BaseModel
from redis.exceptions import RedisError

from api.configs import app_config
from api.exceptions import UnauthorizedError
from api.extensions.ext_redis import redis_client

from .csrf import generate_csrf_token
from .datetime_utils import utc_now
from .passport import PassportService

logger = logging.getLogger(__name__)

REFRESH_TOKEN_PREFIX = "refresh_token:"
REFRESH_TOKEN_ACCESS_PREFIX = "refresh_token_access:"
ACCOUNT_REFRESH_TOKENS_PREFIX = "account_refresh_tokens:"
ACCESS_TOKEN_DENYLIST_PREFIX = "access_token_denylist:"
ACCOUNT_ACCESS_REVOKED_BEFORE_PREFIX = "account_access_revoked_before:"
REFRESH_TOKEN_EXPIRY = timedelta(days=app_config.REFRESH_TOKEN_EXPIRE_DAYS)
ACCESS_TOKEN_EXPIRY = timedelta(minutes=app_config.ACCESS_TOKEN_EXPIRE_MINUTES)


class AuthTokens(BaseModel):
    access_token: str
    refresh_token: str
    csrf_token: str
    access_expires_at: int
    refresh_expires_at: int


@dataclass(frozen=True)
class RefreshSession:
    account_id: str
    access_jti: str | None


def _refresh_token_digest(refresh_token: str) -> str:
    return hashlib.sha256(refresh_token.encode()).hexdigest()


def _refresh_token_key(digest: str) -> str:
    return f"{REFRESH_TOKEN_PREFIX}{digest}"


def _refresh_token_access_key(digest: str) -> str:
    return f"{REFRESH_TOKEN_ACCESS_PREFIX}{digest}"


def _account_refresh_tokens_key(account_id: str) -> str:
    return f"{ACCOUNT_REFRESH_TOKENS_PREFIX}{account_id}"


async def _store_refresh_token(refresh_token: str, account_id: str, access_token: str) -> None:
    digest = _refresh_token_digest(refresh_token)
    tokens_key = _account_refresh_tokens_key(account_id)
    access_jti = PassportService().verify(access_token)["jti"]
    pipe = redis_client.pipeline(transaction=False)
    pipe.setex(_refresh_token_key(digest), REFRESH_TOKEN_EXPIRY, account_id)
    pipe.setex(_refresh_token_access_key(digest), REFRESH_TOKEN_EXPIRY, access_jti)
    pipe.sadd(tokens_key, digest)
    pipe.expire(tokens_key, REFRESH_TOKEN_EXPIRY)
    await pipe.execute()


async def consume_refresh_token(refresh_token: str) -> RefreshSession | None:
    digest = _refresh_token_digest(refresh_token)
    account_id = await redis_client.getdel(_refresh_token_key(digest))
    if not account_id:
        return None
    access_jti = await redis_client.getdel(_refresh_token_access_key(digest))
    await redis_client.srem(_account_refresh_tokens_key(account_id), digest)
    return RefreshSession(account_id=account_id, access_jti=access_jti)


async def revoke_session_tokens(refresh_token: str | None, access_token: str | None) -> None:
    try:
        if refresh_token:
            session = await consume_refresh_token(refresh_token)
            if session is not None:
                await revoke_access_token_id(session.access_jti)
    except RedisError:
        logger.warning("Failed to revoke refresh token during logout", exc_info=True)

    try:
        if access_token:
            await revoke_access_token(access_token)
    except RedisError:
        logger.warning("Failed to revoke access token during logout", exc_info=True)


async def revoke_all_sessions(account_id: str) -> None:
    tokens_key = _account_refresh_tokens_key(account_id)
    digests = await redis_client.smembers(tokens_key)
    await asyncio.gather(
        *(
            redis_client.delete(_refresh_token_key(digest), _refresh_token_access_key(digest))
            for digest in digests or ()
        ),
        redis_client.delete(tokens_key),
        redis_client.setex(
            f"{ACCOUNT_ACCESS_REVOKED_BEFORE_PREFIX}{account_id}",
            ACCESS_TOKEN_EXPIRY,
            int(utc_now().timestamp()),
        ),
    )


async def revoke_access_token_id(access_jti: str | None) -> None:
    if access_jti:
        await redis_client.setex(f"{ACCESS_TOKEN_DENYLIST_PREFIX}{access_jti}", ACCESS_TOKEN_EXPIRY, "1")


async def revoke_access_token(access_token: str) -> None:
    try:
        claims = PassportService().verify(access_token)
    except UnauthorizedError:
        return
    jti = claims.get("jti")
    exp = claims.get("exp")
    if not jti or exp is None:
        return
    ttl = int(exp - utc_now().timestamp())
    if ttl > 0:
        await redis_client.setex(f"{ACCESS_TOKEN_DENYLIST_PREFIX}{jti}", ttl, "1")


async def is_access_token_revoked(claims: dict[str, Any]) -> bool:
    jti = claims.get("jti")
    denied, revoked_before = await asyncio.gather(
        redis_client.get(f"{ACCESS_TOKEN_DENYLIST_PREFIX}{jti}"),
        redis_client.get(f"{ACCOUNT_ACCESS_REVOKED_BEFORE_PREFIX}{claims.get('sub')}"),
    )
    if jti and denied:
        return True
    iat = claims.get("iat")
    return revoked_before is not None and iat is not None and int(iat) < int(revoked_before)


async def issue_tokens(account_id: str) -> AuthTokens:
    now = utc_now()
    access_token = PassportService().issue(
        {"sub": account_id, "jti": secrets.token_hex(16)},
        expires_in=ACCESS_TOKEN_EXPIRY,
    )
    refresh_token = secrets.token_hex(64)
    csrf_token = generate_csrf_token(refresh_token)

    await _store_refresh_token(refresh_token, account_id, access_token)

    return AuthTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
        access_expires_at=int((now + ACCESS_TOKEN_EXPIRY).timestamp()),
        refresh_expires_at=int((now + REFRESH_TOKEN_EXPIRY).timestamp()),
    )
