import asyncio
import functools
import logging
import weakref
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, cast

import redis
from fastapi import FastAPI
from redis.asyncio import Redis
from redis.exceptions import RedisError

from api.configs import app_config

logger = logging.getLogger(__name__)

_SOCKET_TIMEOUT_SECONDS = 5
_SOCKET_CONNECT_TIMEOUT_SECONDS = 5
_HEALTH_CHECK_INTERVAL_SECONDS = 30


class RedisClientWrapper:
    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Redis] = weakref.WeakKeyDictionary()

    def configure(self, **options: Any) -> None:
        self._options = options

    def _require_client(self) -> Redis:
        loop = asyncio.get_running_loop()
        client = self._clients.get(loop)
        if client is None:
            if not self._options:
                raise RuntimeError("Redis client is not configured")
            client = Redis(**self._options)
            self._clients[loop] = client
        return client

    async def get(self, name: str | bytes) -> str | None:
        return await self._require_client().get(name)

    async def setex(self, name: str | bytes, time: int | timedelta, value: Any) -> Any:
        return await self._require_client().setex(name, time, value)

    async def delete(self, *names: str | bytes) -> Any:
        return await self._require_client().delete(*names)

    async def srem(self, name: str | bytes, *values: str | bytes) -> Any:
        return await cast(Any, self._require_client()).srem(name, *values)

    async def smembers(self, name: str | bytes) -> Any:
        return await cast(Any, self._require_client()).smembers(name)

    async def getdel(self, name: str | bytes) -> str | None:
        return await self._require_client().getdel(name)

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        return await cast(Any, self._require_client()).eval(script, numkeys, *keys_and_args)

    def pipeline(self, *, transaction: bool = True) -> Any:
        return self._require_client().pipeline(transaction=transaction)

    async def aclose(self) -> None:
        loop = asyncio.get_running_loop()
        client = self._clients.pop(loop, None)
        if client is not None:
            await client.aclose()


redis_client = RedisClientWrapper()


def create_sync_client() -> redis.Redis:
    return redis.Redis(
        host=app_config.REDIS_HOST,
        port=app_config.REDIS_PORT,
        username=app_config.REDIS_USERNAME or None,
        password=app_config.REDIS_PASSWORD or None,
        db=app_config.REDIS_DB,
        decode_responses=False,
        socket_timeout=_SOCKET_TIMEOUT_SECONDS,
        socket_connect_timeout=_SOCKET_CONNECT_TIMEOUT_SECONDS,
        health_check_interval=_HEALTH_CHECK_INTERVAL_SECONDS,
    )


def init_app(app: FastAPI) -> None:
    redis_client.configure(
        host=app_config.REDIS_HOST,
        port=app_config.REDIS_PORT,
        username=app_config.REDIS_USERNAME or None,
        password=app_config.REDIS_PASSWORD or None,
        db=app_config.REDIS_DB,
        decode_responses=True,
        protocol=3,
        socket_timeout=_SOCKET_TIMEOUT_SECONDS,
        socket_connect_timeout=_SOCKET_CONNECT_TIMEOUT_SECONDS,
        health_check_interval=_HEALTH_CHECK_INTERVAL_SECONDS,
    )


def redis_fallback[T, **P, R](
    default_return: T | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R | T | None]]]:
    def decorator(
        func: Callable[P, Awaitable[R]],
    ) -> Callable[P, Awaitable[R | T | None]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R | T | None:
            try:
                return await func(*args, **kwargs)
            except RedisError as error:
                func_name = getattr(func, "__name__", "Unknown")
                logger.warning("Redis operation failed in %s: %s", func_name, str(error), exc_info=True)
                return default_return

        return wrapper

    return decorator
