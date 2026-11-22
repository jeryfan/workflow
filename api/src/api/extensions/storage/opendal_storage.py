from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import boto3
import opendal
from botocore.exceptions import ClientError

from api.configs import app_config
from api.exceptions import NotFoundError

from .base_storage import BaseStorage

logger = logging.getLogger(__name__)

_STREAM_CHUNK_SIZE = 4096
_DOWNLOAD_CHUNK_SIZE = 8 * 1024 * 1024


def _get_opendal_kwargs(*, scheme: str, prefix: str = "OPENDAL_") -> dict[str, str]:
    config_prefix = prefix + scheme.upper() + "_"
    return {
        key[len(config_prefix) :].lower(): value for key, value in os.environ.items() if key.startswith(config_prefix)
    }


def _ensure_s3_bucket(kwargs: dict[str, str]) -> None:
    bucket = kwargs["bucket"]
    region = kwargs.get("region", "us-east-1")
    client = boto3.client(
        "s3",
        region_name=region,
        endpoint_url=kwargs.get("endpoint"),
        aws_access_key_id=kwargs.get("access_key_id"),
        aws_secret_access_key=kwargs.get("secret_access_key"),
    )
    try:
        try:
            client.head_bucket(Bucket=bucket)
            return
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
        parameters = {"Bucket": bucket}
        if region != "us-east-1":
            parameters["CreateBucketConfiguration"] = {"LocationConstraint": region}
        client.create_bucket(**parameters)
    finally:
        client.close()


@asynccontextmanager
async def _open_reader(
    op: opendal.AsyncOperator,
    filename: str,
) -> AsyncGenerator[opendal.AsyncFile]:
    file = await op.open(path=filename, mode="rb")
    try:
        yield file
    finally:
        await file.close()


class AsyncOpenDALStorage(BaseStorage):
    def __init__(self, scheme: str, **kwargs: str) -> None:
        kwargs = {**_get_opendal_kwargs(scheme=scheme), **kwargs}
        if scheme == "fs":
            root = kwargs.get("root") or app_config.OPENDAL_FS_ROOT
            Path(root).mkdir(parents=True, exist_ok=True)
            kwargs["root"] = root
        elif scheme == "s3":
            _ensure_s3_bucket(kwargs)

        self.op = opendal.AsyncOperator(scheme=scheme, **kwargs)
        logger.debug("opendal operator created with scheme %s", scheme)
        retry_layer = opendal.layers.RetryLayer(max_times=3, factor=2.0, jitter=True)
        self.op = self.op.layer(retry_layer)

    async def save(self, filename: str, data: bytes) -> None:
        await self.op.write(path=filename, bs=data)
        logger.debug("file %s saved", filename)

    async def load_once(self, filename: str) -> bytes:
        try:
            content = await self.op.read(path=filename)
        except opendal.exceptions.NotFound:
            raise NotFoundError("File not found") from None
        logger.debug("file %s loaded", filename)
        return content

    def load_stream(self, filename: str) -> AsyncGenerator[bytes]:
        async def _stream() -> AsyncGenerator[bytes]:
            try:
                async with _open_reader(self.op, filename) as file:
                    while chunk := await file.read(_STREAM_CHUNK_SIZE):
                        yield chunk
            except opendal.exceptions.NotFound:
                raise NotFoundError("File not found") from None
            logger.debug("file %s loaded as stream", filename)

        return _stream()

    async def download(self, filename: str, target_filepath: str) -> None:
        try:
            async with _open_reader(self.op, filename) as remote:
                out = await asyncio.to_thread(Path(target_filepath).open, "wb")
                try:
                    try:
                        while chunk := await remote.read(_DOWNLOAD_CHUNK_SIZE):
                            await asyncio.to_thread(out.write, chunk)
                    finally:
                        await asyncio.to_thread(out.close)
                except BaseException:
                    await asyncio.to_thread(Path(target_filepath).unlink, missing_ok=True)
                    raise
        except opendal.exceptions.NotFound:
            raise NotFoundError("File not found") from None
        logger.debug("file %s downloaded to %s", filename, target_filepath)

    async def exists(self, filename: str) -> bool:
        return await self.op.exists(path=filename)

    async def delete(self, filename: str) -> None:
        await self.op.delete(path=filename)
        logger.debug("file %s deleted", filename)

    async def list(self, path: str, files: bool = True, directories: bool = False) -> list[str]:
        if not files and not directories:
            raise ValueError("At least one of files or directories must be True")

        normalized = path if path.endswith("/") else f"{path}/"
        try:
            entries = await self.op.list(path=normalized)
        except opendal.exceptions.NotFound:
            raise NotFoundError("Path not found") from None

        result = [
            entry.path
            async for entry in entries
            if entry.path != normalized
            and ((files and entry.metadata.is_file) or (directories and entry.metadata.is_dir))
        ]
        logger.debug("path %s scanned", normalized)
        return result
