from __future__ import annotations

from api.config import get_settings
from api.db.store import Store
from api.services.file_storage import S3ObjectStorage
from api.services.files import FileService


def create_file_service(store: Store) -> FileService:
    settings = get_settings()
    storage = None
    if settings.storage_backend == "s3":
        storage = S3ObjectStorage(
            bucket=settings.s3_bucket,
            region=settings.s3_region,
            endpoint_url=settings.s3_endpoint_url,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
        )
    return FileService(
        store,
        storage,
        signing_key=settings.file_signing_key.encode() if settings.file_signing_key else None,
        public_api_url=settings.public_api_url,
    )
