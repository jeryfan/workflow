from urllib.parse import quote

from pydantic import Field

from .redis_config import RedisConfig


class CeleryConfig(RedisConfig):
    @property
    def CELERY_BROKER_URL(self) -> str:
        username = quote(self.REDIS_USERNAME or "", safe="")
        password = quote(self.REDIS_PASSWORD or "", safe="")
        credentials = f"{username}:{password}@" if username or password else ""
        return f"redis://{credentials}{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    TASK_TIME_LIMIT_SECONDS: int = Field(
        description="Hard time limit for workflow execution tasks in seconds.",
        default=3600,
        ge=60,
    )
    TASK_SOFT_TIME_LIMIT_SECONDS: int = Field(
        description="Soft time limit for workflow execution tasks in seconds.",
        default=3500,
        ge=30,
    )
