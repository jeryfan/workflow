import logging
from collections.abc import AsyncGenerator

from fastapi import FastAPI

from api.configs import app_config
from api.constants.storage import StorageType

from .storage.base_storage import BaseStorage
from .storage.opendal_storage import AsyncOpenDALStorage

logger = logging.getLogger(__name__)


class Storage:
    def __init__(self) -> None:
        self._storage_runner: BaseStorage | None = None

    def init_app(self) -> None:
        match app_config.STORAGE_TYPE:
            case StorageType.OPENDAL:
                self._storage_runner = AsyncOpenDALStorage(
                    app_config.OPENDAL_SCHEME,
                    **_opendal_kwargs(app_config.OPENDAL_SCHEME),
                )
            case StorageType.LOCAL:
                self._storage_runner = AsyncOpenDALStorage("fs", root=app_config.STORAGE_LOCAL_PATH)

    def _require_runner(self) -> BaseStorage:
        if self._storage_runner is None:
            raise RuntimeError("Storage is not initialized. Call init_app first.")
        return self._storage_runner

    async def save(self, filename: str, data: bytes) -> None:
        await self._require_runner().save(filename, data)

    async def load_once(self, filename: str) -> bytes:
        return await self._require_runner().load_once(filename)

    def load_stream(self, filename: str) -> AsyncGenerator[bytes]:
        return self._require_runner().load_stream(filename)

    async def download(self, filename: str, target_filepath: str) -> None:
        await self._require_runner().download(filename, target_filepath)

    async def exists(self, filename: str) -> bool:
        return await self._require_runner().exists(filename)

    async def delete(self, filename: str) -> None:
        await self._require_runner().delete(filename)

    async def list(self, path: str, files: bool = True, directories: bool = False) -> list[str]:
        return await self._require_runner().list(path, files=files, directories=directories)


def _opendal_kwargs(scheme: str) -> dict[str, str]:
    if scheme == "fs":
        return {"root": app_config.OPENDAL_FS_ROOT}

    kwargs = {
        "bucket": app_config.OPENDAL_S3_BUCKET,
        "region": app_config.OPENDAL_S3_REGION,
        "endpoint": app_config.OPENDAL_S3_ENDPOINT,
        "access_key_id": app_config.OPENDAL_S3_ACCESS_KEY_ID,
        "secret_access_key": app_config.OPENDAL_S3_SECRET_ACCESS_KEY,
    }
    return {key: value for key, value in kwargs.items() if value is not None}


storage = Storage()


def init_app(app: FastAPI) -> None:
    storage.init_app()
