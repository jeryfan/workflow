from pydantic import Field, PositiveInt

from api.constants.storage import StorageType

from .opendal_storage_config import OpenDALStorageConfig


class StorageConfig(OpenDALStorageConfig):
    STORAGE_TYPE: StorageType = Field(
        description="Storage implementation. 'opendal' uses OPENDAL_SCHEME; 'local' uses STORAGE_LOCAL_PATH.",
        default=StorageType.OPENDAL,
    )
    STORAGE_LOCAL_PATH: str = Field(
        description="Filesystem path used when STORAGE_TYPE is 'local'.",
        default=".data/storage",
    )
    MAX_UPLOAD_FILE_SIZE: PositiveInt = Field(
        description="Maximum upload file size in bytes.",
        default=20 * 1024 * 1024,
    )
