from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class OpenDALStorageConfig(BaseSettings):
    OPENDAL_SCHEME: Literal["fs", "s3"] = Field(
        default="fs",
        description="OpenDAL storage scheme. Supported schemes are 'fs' and 's3'.",
    )
    OPENDAL_FS_ROOT: str = Field(
        default=".data/storage",
        description="Filesystem root used by the OpenDAL fs scheme.",
    )
    OPENDAL_S3_BUCKET: str = Field(
        default="workflow",
        description="Bucket used by the OpenDAL s3 scheme.",
    )
    OPENDAL_S3_REGION: str = Field(
        default="us-east-1",
        description="Region used by the OpenDAL s3 scheme.",
    )
    OPENDAL_S3_ENDPOINT: str | None = Field(
        default=None,
        description="Optional endpoint for an S3-compatible service.",
    )
    OPENDAL_S3_ACCESS_KEY_ID: str | None = Field(
        default=None,
        description="Optional S3 access key ID. The provider chain is used when unset.",
    )
    OPENDAL_S3_SECRET_ACCESS_KEY: str | None = Field(
        default=None,
        description="Optional S3 secret access key. The provider chain is used when unset.",
    )
