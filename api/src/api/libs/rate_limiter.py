import secrets
import time

from api.extensions.ext_redis import redis_client, redis_fallback

_RATE_LIMIT_SCRIPT = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then
    return 1
end
redis.call('ZADD', KEYS[1], ARGV[3], ARGV[4])
redis.call('EXPIRE', KEYS[1], ARGV[5])
return 0
"""

_ERROR_LIMIT_SCRIPT = """
if tonumber(redis.call('GET', KEYS[1]) or '0') >= tonumber(ARGV[1]) then
    return -1
end
local count = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ARGV[2])
return count
"""


class RateLimiter:
    def __init__(self, prefix: str, max_attempts: int, time_window: int):
        self.prefix = prefix
        self.max_attempts = max_attempts
        self.time_window = time_window

    def _get_key(self, key: str) -> str:
        return f"{self.prefix}:{key}"

    @redis_fallback(default_return=False)
    async def is_limited(self, key: str) -> bool:
        now = int(time.time())
        member = f"{now}:{secrets.token_urlsafe(nbytes=8)}"
        result = await redis_client.eval(
            _RATE_LIMIT_SCRIPT,
            1,
            self._get_key(key),
            now - self.time_window,
            self.max_attempts,
            now,
            member,
            self.time_window * 2,
        )
        return result == 1

    @redis_fallback(default_return=None)
    async def reset(self, key: str) -> None:
        await redis_client.delete(self._get_key(key))


class ErrorRateLimiter:
    def __init__(self, prefix: str, max_errors: int, lockout_seconds: int):
        self.prefix = prefix
        self.max_errors = max_errors
        self.lockout_seconds = lockout_seconds

    def _get_key(self, key: str) -> str:
        return f"{self.prefix}:{key}"

    @redis_fallback(default_return=False)
    async def is_limited(self, key: str) -> bool:
        count = await redis_client.get(self._get_key(key))
        return count is not None and int(count) >= self.max_errors

    @redis_fallback(default_return=None)
    async def increment(self, key: str) -> None:
        await redis_client.eval(
            _ERROR_LIMIT_SCRIPT,
            1,
            self._get_key(key),
            self.max_errors,
            self.lockout_seconds,
        )

    @redis_fallback(default_return=None)
    async def reset(self, key: str) -> None:
        await redis_client.delete(self._get_key(key))
