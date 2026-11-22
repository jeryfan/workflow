from __future__ import annotations

import asyncio
import os
from collections.abc import Generator
from typing import Literal

import httpx

from api.core.graphon.file.models import File
from api.extensions.ext_storage import storage

from .files import FileService


class WorkflowFileRuntime:
    """Synchronous Graphon file adapter used inside workflow worker threads."""

    def __init__(self, files: FileService) -> None:
        self._files = files

    @property
    def multimodal_send_format(self) -> str:
        return "url"

    def http_get(self, url: str, *, follow_redirects: bool = True) -> httpx.Response:
        proxy = os.environ.get("HTTP_REQUEST_PROXY", "http://ssrf-proxy:3128")
        with httpx.Client(proxy=proxy or None, trust_env=False, timeout=60) as client:
            return client.get(url, follow_redirects=follow_redirects)

    def storage_load(self, path: str, *, stream: bool = False) -> bytes | Generator:
        _ = stream
        return asyncio.run(storage.load_once(path))

    def load_file_bytes(self, *, file: File) -> bytes:
        if not file.storage_key:
            raise ValueError("File has no stored content")
        return asyncio.run(storage.load_once(file.storage_key))

    def resolve_file_url(self, *, file: File, for_external: bool = True) -> str | None:
        _ = for_external
        if file.related_id:
            return self._files.signed_url(file.related_id)
        return file.remote_url

    def resolve_upload_file_url(
        self,
        *,
        upload_file_id: str,
        as_attachment: bool = False,
        for_external: bool = True,
    ) -> str:
        _ = as_attachment, for_external
        return self._files.signed_url(upload_file_id)

    def resolve_tool_file_url(
        self,
        *,
        tool_file_id: str,
        extension: str,
        for_external: bool = True,
    ) -> str:
        _ = tool_file_id, extension, for_external
        raise ValueError("Tool files are not supported")

    def verify_preview_signature(
        self,
        *,
        preview_kind: Literal["image", "file"],
        file_id: str,
        timestamp: str,
        nonce: str,
        sign: str,
    ) -> bool:
        _ = preview_kind, file_id, timestamp, nonce, sign
        raise ValueError("Legacy preview signatures are not supported")
