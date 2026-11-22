import os
import threading
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-at-least-32-bytes")
os.environ.setdefault("FILE_SIGNING_KEY", "test-signing-key-that-is-at-least-32-bytes")
os.environ.setdefault("DB_PASSWORD", "workflow-test")
os.environ.setdefault("APP_ENV", "test")

from api.configs import app_config


class InMemoryRedis:
    """Small Redis test double with atomic token consumption and rate limiting."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.expirations: dict[str, float] = {}
        self.lock = threading.Lock()

    @staticmethod
    def _name(name: str | bytes) -> str:
        return name.decode() if isinstance(name, bytes) else name

    def _purge(self, name: str) -> None:
        expires_at = self.expirations.get(name)
        if expires_at is not None and expires_at <= time.monotonic():
            self.values.pop(name, None)
            self.sets.pop(name, None)
            self.expirations.pop(name, None)

    async def get(self, name: str | bytes) -> str | None:
        key = self._name(name)
        with self.lock:
            self._purge(key)
            return self.values.get(key)

    async def getdel(self, name: str | bytes) -> str | None:
        key = self._name(name)
        with self.lock:
            self._purge(key)
            self.expirations.pop(key, None)
            return self.values.pop(key, None)

    async def setex(self, name: str | bytes, duration: int | timedelta, value: Any) -> bool:
        key = self._name(name)
        seconds = duration.total_seconds() if isinstance(duration, timedelta) else duration
        with self.lock:
            self.values[key] = str(value)
            self.expirations[key] = time.monotonic() + seconds
        return True

    async def delete(self, *names: str | bytes) -> int:
        deleted = 0
        with self.lock:
            for raw_name in names:
                key = self._name(raw_name)
                deleted += key in self.values or key in self.sets
                self.values.pop(key, None)
                self.sets.pop(key, None)
                self.expirations.pop(key, None)
        return deleted

    async def srem(self, name: str | bytes, *values: str | bytes) -> int:
        key = self._name(name)
        with self.lock:
            members = self.sets.get(key, set())
            before = len(members)
            members.difference_update(self._name(value) for value in values)
            if not members:
                self.sets.pop(key, None)
            return before - len(members)

    async def smembers(self, name: str | bytes) -> set[str]:
        key = self._name(name)
        with self.lock:
            self._purge(key)
            return set(self.sets.get(key, set()))

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> int:
        if "INCR" not in script or numkeys != 1:
            raise NotImplementedError("This test double only implements the login error counter script")
        key = self._name(keys_and_args[0])
        maximum, lockout_seconds = (int(value) for value in keys_and_args[1:])
        with self.lock:
            self._purge(key)
            count = int(self.values.get(key, "0"))
            if count >= maximum:
                return -1
            count += 1
            self.values[key] = str(count)
            self.expirations[key] = time.monotonic() + lockout_seconds
            return count

    def pipeline(self, *, transaction: bool = True) -> "InMemoryPipeline":
        return InMemoryPipeline(self)


class InMemoryPipeline:
    def __init__(self, client: InMemoryRedis) -> None:
        self.client = client
        self.operations: list[tuple[str, tuple[Any, ...]]] = []

    def setex(self, name: str | bytes, duration: int | timedelta, value: Any) -> "InMemoryPipeline":
        self.operations.append(("setex", (name, duration, value)))
        return self

    def sadd(self, name: str | bytes, *values: str | bytes) -> "InMemoryPipeline":
        self.operations.append(("sadd", (name, *values)))
        return self

    def expire(self, name: str | bytes, duration: int | timedelta) -> "InMemoryPipeline":
        self.operations.append(("expire", (name, duration)))
        return self

    async def execute(self) -> list[Any]:
        results = []
        with self.client.lock:
            for operation, arguments in self.operations:
                if operation == "setex":
                    name, duration, value = arguments
                    key = self.client._name(name)
                    seconds = duration.total_seconds() if isinstance(duration, timedelta) else duration
                    self.client.values[key] = str(value)
                    self.client.expirations[key] = time.monotonic() + seconds
                    results.append(True)
                elif operation == "sadd":
                    name, *values = arguments
                    key = self.client._name(name)
                    members = self.client.sets.setdefault(key, set())
                    before = len(members)
                    members.update(self.client._name(value) for value in values)
                    results.append(len(members) - before)
                else:
                    name, duration = arguments
                    key = self.client._name(name)
                    seconds = duration.total_seconds() if isinstance(duration, timedelta) else duration
                    self.client.expirations[key] = time.monotonic() + seconds
                    results.append(True)
        self.operations.clear()
        return results


@pytest.fixture(autouse=True)
def isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    values = {
        "APP_ENV": "test",
        "DEBUG": False,
        "EXECUTION_BACKEND": "local",
        "STORAGE_TYPE": "local",
        "STORAGE_LOCAL_PATH": str(tmp_path / "storage"),
        "OPENDAL_SCHEME": "fs",
        "OPENDAL_FS_ROOT": str(tmp_path / "storage"),
        "FILE_SIGNING_KEY": "test-signing-key-that-is-at-least-32-bytes",
        "PUBLIC_API_URL": "http://127.0.0.1:8000",
        "PUBLIC_WEB_URL": "http://localhost",
    }
    for key, value in values.items():
        monkeypatch.setattr(app_config, key, value)
    yield


@pytest.fixture
def redis_backend() -> InMemoryRedis:
    return InMemoryRedis()


@pytest.fixture(autouse=True)
def isolate_redis(redis_backend: InMemoryRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    from api.extensions.ext_redis import RedisClientWrapper

    monkeypatch.setattr(RedisClientWrapper, "_require_client", lambda _self: redis_backend)


class InMemorySyncRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lock = threading.Lock()

    def set(self, name: str, value: str, ex: int | None = None) -> bool:
        with self.lock:
            self.values[name] = value
        return True

    def get(self, name: str) -> str | None:
        with self.lock:
            return self.values.get(name)

    def delete(self, *names: str) -> int:
        with self.lock:
            return sum(self.values.pop(name, None) is not None for name in names)

    def close(self) -> None:
        return


@pytest.fixture(autouse=True)
def isolate_sync_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    client = InMemorySyncRedis()
    monkeypatch.setattr("api.extensions.ext_redis.create_sync_client", lambda: client)
