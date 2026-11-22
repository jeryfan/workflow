from __future__ import annotations

import contextlib
import hashlib
import hmac
import logging
import mimetypes
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse
from uuid import uuid4

import httpx

from api.config import PROJECT_ROOT
from api.db.store import Store
from api.models.entities import AppId, UploadedFile
from api.services.file_storage import LocalObjectStorage, ObjectStorage
from graphon.file.enums import FileTransferMethod, FileType
from graphon.file.models import File

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
BLOCKED_EXTENSIONS = frozenset({".exe", ".bat", ".cmd", ".com", ".msi", ".jar", ".apk", ".dll", ".so", ".dylib"})
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"})
AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".avi", ".mkv", ".webm", ".mpeg"})
DOCUMENT_EXTENSIONS = frozenset(
    {".txt", ".md", ".markdown", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".json", ".yaml", ".yml"}
)
TEXT_EXTENSIONS = frozenset({".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".log"})
logger = logging.getLogger(__name__)


class FileError(ValueError):
    pass


class FileService:
    def __init__(
        self,
        store: Store,
        storage: ObjectStorage | None = None,
        *,
        signing_key: bytes | None = None,
        public_api_url: str | None = None,
    ) -> None:
        self.store = store
        database = store.engine.url.database
        if store.engine.dialect.name == "sqlite" and database and database != ":memory:":
            root = Path(database).resolve().parent
        else:
            root = PROJECT_ROOT / ".data"
        self.upload_root = root / "uploads"
        self.storage = storage or LocalObjectStorage(self.upload_root)
        self.public_api_url = public_api_url
        self._signing_key_path = root / ".file-signing-key"
        if signing_key is not None:
            if len(signing_key) < 32:
                raise ValueError("File signing key must contain at least 32 bytes")
            self._signing_key = signing_key
            return
        try:
            self._signing_key = self._signing_key_path.read_bytes()
        except FileNotFoundError:
            self._signing_key = secrets.token_bytes(32)
            self._signing_key_path.parent.mkdir(parents=True, exist_ok=True)
            self._signing_key_path.write_bytes(self._signing_key)
            with contextlib.suppress(OSError):
                self._signing_key_path.chmod(0o600)

    @staticmethod
    def normalize_filename(value: str) -> str:
        name = value.replace("\\", "/").split("/")[-1].replace("\x00", "").strip()
        if not name:
            raise FileError("Filename is required")
        return name[:255]

    @staticmethod
    def extension(name: str) -> str:
        return Path(name).suffix.lower()

    @staticmethod
    def file_type(file: UploadedFile) -> FileType:
        extension = file.extension
        mime = file.mime_type.lower()
        if mime.startswith("image/") or extension in IMAGE_EXTENSIONS:
            return FileType.IMAGE
        if mime.startswith("audio/") or extension in AUDIO_EXTENSIONS:
            return FileType.AUDIO
        if mime.startswith("video/") or extension in VIDEO_EXTENSIONS:
            return FileType.VIDEO
        if extension in DOCUMENT_EXTENSIONS or mime.startswith("text/") or mime == "application/pdf":
            return FileType.DOCUMENT
        return FileType.CUSTOM

    def save(
        self,
        *,
        app_id: AppId | None,
        actor_id: str,
        filename: str,
        content: bytes,
        mime_type: str | None,
        source_url: str | None = None,
    ) -> UploadedFile:
        name = self.normalize_filename(filename)
        if len(content) > MAX_UPLOAD_BYTES:
            raise FileError("File size exceeds the 20 MB limit")
        extension = self.extension(name)
        if extension in BLOCKED_EXTENSIONS:
            raise FileError(f"File extension is blocked: {extension}")
        file_id = str(uuid4())
        storage_key = f"{file_id}{extension}"
        try:
            self.storage.write(storage_key, content)
            file = UploadedFile(
                id=file_id,
                app_id=app_id,
                name=name,
                extension=extension,
                mime_type=(mime_type or mimetypes.guess_type(name)[0] or "application/octet-stream").lower(),
                size=len(content),
                storage_key=storage_key,
                source_url=source_url,
                created_by=actor_id,
                created_at=int(time.time()),
            )
            self.store.create_uploaded_file(file)
            return file
        except Exception:
            with contextlib.suppress(Exception):
                self.storage.delete(storage_key)
            raise

    def fetch_remote(self, *, app_id: AppId | None, actor_id: str, url: str) -> UploadedFile:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise FileError("A valid HTTP or HTTPS file URL is required")
        proxy = os.environ.get("HTTP_REQUEST_PROXY", "http://ssrf_proxy:3128")
        try:
            with (
                httpx.Client(
                    proxy=proxy or None,
                    trust_env=False,
                    follow_redirects=True,
                    timeout=httpx.Timeout(20.0, read=20.0),
                ) as client,
                client.stream("GET", url, headers={"Accept": "*/*"}) as response,
            ):
                if response.status_code != 200:
                    raise FileError(f"Remote file returned HTTP {response.status_code}")
                declared = response.headers.get("Content-Length")
                if declared and int(declared) > MAX_UPLOAD_BYTES:
                    raise FileError("Remote file size exceeds the 20 MB limit")
                chunks = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise FileError("Remote file size exceeds the 20 MB limit")
                    chunks.append(chunk)
                content = b"".join(chunks)
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
                final_url = str(response.url)
        except (httpx.HTTPError, ValueError) as error:
            if isinstance(error, FileError):
                raise
            raise FileError("Unable to download the remote file") from error
        name = Path(urlparse(final_url).path).name or "remote-file"
        if not Path(name).suffix and content_type:
            name += mimetypes.guess_extension(content_type) or ""
        return self.save(
            app_id=app_id,
            actor_id=actor_id,
            filename=name,
            content=content,
            mime_type=content_type or None,
            source_url=final_url,
        )

    def response(self, file: UploadedFile) -> dict[str, Any]:
        url = self.signed_url(file.id)
        return {
            "id": file.id,
            "name": file.name,
            "size": file.size,
            "extension": file.extension,
            "mime_type": file.mime_type,
            "created_at": file.created_at,
            "created_by": file.created_by,
            "preview_url": url,
            "source_url": file.source_url or url,
            "url": url,
        }

    def signed_url(self, file_id: str, *, expires_in: int = 3600) -> str:
        expires = int(time.time()) + expires_in
        signature = hmac.new(
            self._signing_key,
            f"{file_id}:{expires}".encode(),
            hashlib.sha256,
        ).hexdigest()
        base_url = (self.public_api_url or os.environ.get("CONSOLE_API_URL", "http://127.0.0.1:5001")).rstrip("/")
        return f"{base_url}/api/files/{file_id}/content?{urlencode({'expires': expires, 'signature': signature})}"

    def content(self, file_id: str, *, expires: int, signature: str) -> tuple[UploadedFile, bytes]:
        if expires < int(time.time()):
            raise FileError("File URL has expired")
        expected = hmac.new(
            self._signing_key,
            f"{file_id}:{expires}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise FileError("Invalid file signature")
        file = self.store.get_uploaded_file(file_id)
        if file is None:
            raise FileError("File not found")
        try:
            content = self.storage.read(file.storage_key)
        except FileNotFoundError as error:
            raise FileError("File content is missing") from error
        if len(content) != file.size:
            raise FileError("File content is incomplete")
        return file, content

    def preview(self, file_id: str, *, account_id: str) -> str:
        file = self.store.get_uploaded_file(file_id)
        if file is None or file.created_by != account_id:
            raise FileError("File not found")
        if not (
            file.mime_type.startswith("text/")
            or file.extension in TEXT_EXTENSIONS
            or file.mime_type in {"application/json", "application/x-yaml"}
        ):
            raise FileError("Only text file previews are supported")
        content = self.storage.read(file.storage_key)[:256_000]
        return content.decode("utf-8", errors="replace")

    def delete_content(self, files: tuple[UploadedFile, ...]) -> None:
        for file in files:
            try:
                self.storage.delete(file.storage_key)
            except Exception as error:
                logger.warning("Unable to remove content for deleted file %s: %s", file.id, error)

    def to_graphon_file(self, value: Any, *, account_id: str, app_id: AppId) -> File:
        if not isinstance(value, dict):
            raise FileError("File input must be an object")
        file_id = value.get("upload_file_id") or value.get("related_id") or value.get("uploadedId")
        if not isinstance(file_id, str) or not file_id:
            raise FileError("File must be uploaded before running the workflow")
        file = self.store.get_uploaded_file(file_id)
        if file is None or (file.created_by != account_id and file.app_id != app_id):
            raise FileError("File is not available to the current account or app")
        transfer_method = FileTransferMethod.REMOTE_URL if file.source_url else FileTransferMethod.LOCAL_FILE
        return File(
            upload_file_id=file.id,
            file_type=self.file_type(file),
            transfer_method=transfer_method,
            remote_url=file.source_url,
            filename=file.name,
            extension=file.extension,
            mime_type=file.mime_type,
            size=file.size,
            storage_key=file.storage_key,
        )

    def convert_input(self, value: Any, variable: dict[str, Any], *, account_id: str, app_id: AppId) -> Any:
        variable_type = variable.get("type")
        if variable_type == "file":
            if value is None or value == "":
                if variable.get("required", True):
                    raise FileError(f"Required file input is missing: {variable.get('variable')}")
                return value
            return self.to_graphon_file(value, account_id=account_id, app_id=app_id)
        if variable_type == "file-list":
            if not isinstance(value, list):
                raise FileError(f"File list input must be an array: {variable.get('variable')}")
            if not value and variable.get("required", True):
                raise FileError(f"Required file list is empty: {variable.get('variable')}")
            return [self.to_graphon_file(item, account_id=account_id, app_id=app_id) for item in value]
        return value

    def add_urls_to_value(self, value: Any) -> Any:
        if isinstance(value, list):
            return [self.add_urls_to_value(item) for item in value]
        if not isinstance(value, dict):
            return value
        file_id = value.get("related_id") or value.get("upload_file_id") or value.get("reference")
        if (
            (value.get("dify_model_identity") == "__dify__file__" or value.get("upload_file_id"))
            and isinstance(file_id, str)
            and self.store.get_uploaded_file(file_id) is not None
        ):
            return {
                **value,
                "related_id": file_id,
                "upload_file_id": file_id,
                "url": self.signed_url(file_id),
            }
        return {key: self.add_urls_to_value(item) for key, item in value.items()}
