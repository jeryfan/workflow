from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from datetime import timedelta
from typing import Any


class BaseStorage(ABC):
    @abstractmethod
    async def save(self, filename: str, data: bytes) -> None:
        raise NotImplementedError

    @abstractmethod
    async def load_once(self, filename: str) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def load_stream(self, filename: str) -> AsyncGenerator[bytes]:
        raise NotImplementedError

    @abstractmethod
    async def download(self, filename: str, target_filepath: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def exists(self, filename: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, filename: str) -> None:
        raise NotImplementedError

    async def list(self, path: str, files: bool = True, directories: bool = False) -> list[str]:
        raise NotImplementedError("This storage backend doesn't support scanning")

    async def generate_presigned_url(
        self,
        filename: str,
        expires_in: timedelta = timedelta(hours=1),
        content_type: str | None = None,
    ) -> str:
        raise NotImplementedError("This storage backend doesn't support presigned URLs")

    async def generate_presigned_upload_url(
        self,
        filename: str,
        expires_in: timedelta = timedelta(hours=1),
        content_type: str | None = None,
    ) -> str:
        raise NotImplementedError("This storage backend doesn't support presigned upload URLs")

    async def get_file_size(self, filename: str) -> int:
        raise NotImplementedError("This storage backend doesn't support getting file size")

    async def get_file_metadata(self, filename: str) -> dict[str, Any]:
        raise NotImplementedError("This storage backend doesn't support getting file metadata")
