from pydantic import Field, NonNegativeInt, PositiveInt
from pydantic_settings import BaseSettings


class RedisConfig(BaseSettings):
    REDIS_HOST: str = Field(
        description="Hostname or IP address of the Redis server.",
        default="localhost",
    )
    REDIS_PORT: PositiveInt = Field(
        description="Port number on which the Redis server is listening.",
        default=6379,
    )
    REDIS_USERNAME: str | None = Field(
        description="Username for Redis authentication, if required.",
        default=None,
    )
    REDIS_PASSWORD: str | None = Field(
        description="Password for Redis authentication, if required.",
        default=None,
    )
    REDIS_DB: NonNegativeInt = Field(
        description="Redis database number used by application data.",
        default=0,
    )
