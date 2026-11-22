from pydantic import Field, PositiveInt
from pydantic_settings import BaseSettings


class LoggingConfig(BaseSettings):
    LOG_LEVEL: str = Field(
        description="Logging level.",
        default="INFO",
    )
    LOG_FILE: str | None = Field(
        description="File path for log output.",
        default=None,
    )
    LOG_FILE_MAX_SIZE: PositiveInt = Field(
        description="Maximum rotated log file size in megabytes.",
        default=20,
    )
    LOG_FILE_BACKUP_COUNT: PositiveInt = Field(
        description="Maximum number of rotated log files to retain.",
        default=5,
    )
    LOG_FORMAT: str = Field(
        description="Format string for log messages.",
        default=(
            "%(asctime)s.%(msecs)03d %(levelname)s [%(threadName)s] "
            "[%(filename)s:%(lineno)d] %(trace_id)s - %(message)s"
        ),
    )
    LOG_DATEFORMAT: str | None = Field(
        description="Date format string for log timestamps.",
        default=None,
    )
    LOG_TZ: str | None = Field(
        description="Timezone for log timestamps.",
        default="UTC",
    )
