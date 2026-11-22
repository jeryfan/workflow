from typing import Any, TypedDict
from urllib.parse import parse_qsl, quote_plus

from pydantic import Field, NonNegativeInt, PositiveInt
from pydantic_settings import BaseSettings


class SQLAlchemyEngineOptionsDict(TypedDict):
    pool_size: int
    max_overflow: int
    pool_recycle: int
    pool_pre_ping: bool
    connect_args: dict[str, Any]
    pool_use_lifo: bool
    pool_timeout: int


class DatabaseConfig(BaseSettings):
    DB_HOST: str = Field(
        description="Hostname or IP address of the database server.",
        default="localhost",
    )
    DB_PORT: PositiveInt = Field(
        description="Port number for database connection.",
        default=5432,
    )
    DB_USERNAME: str = Field(
        description="Username for database authentication.",
        default="workflow",
    )
    DB_PASSWORD: str = Field(
        description="Password for database authentication.",
    )
    DB_DATABASE: str = Field(
        description="Name of the database to connect to.",
        default="workflow",
    )
    DB_EXTRAS: str = Field(
        description=(
            "Additional database connection parameters. Only 'options' is consumed and passed "
            "via asyncpg server_settings as PostgreSQL startup options. "
            "Example: 'options=-c statement_timeout=30000'"
        ),
        default="",
    )
    DB_SESSION_TIMEZONE_OVERRIDE: str = Field(
        description=(
            "PostgreSQL session timezone injected via startup options. Set to empty string to disable "
            "injection when a pooler rejects the 'options' startup parameter."
        ),
        default="UTC",
    )

    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        return (
            "postgresql+asyncpg://"
            f"{quote_plus(self.DB_USERNAME)}:{quote_plus(self.DB_PASSWORD)}@"
            f"{self.DB_HOST}:{self.DB_PORT}/{self.DB_DATABASE}"
        )

    SQLALCHEMY_POOL_SIZE: PositiveInt = Field(
        description="Maximum number of database connections in the pool.",
        default=15,
    )
    SQLALCHEMY_MAX_OVERFLOW: NonNegativeInt = Field(
        description="Maximum number of connections that can be created beyond the pool size.",
        default=10,
    )
    SQLALCHEMY_POOL_RECYCLE: NonNegativeInt = Field(
        description="Number of seconds after which a connection is automatically recycled.",
        default=3600,
    )
    SQLALCHEMY_POOL_USE_LIFO: bool = Field(
        description="Whether SQLAlchemy retrieves pooled connections in last-in-first-out order.",
        default=False,
    )
    SQLALCHEMY_POOL_PRE_PING: bool = Field(
        description="Whether to check connection liveness before checking it out from the pool.",
        default=True,
    )
    SQLALCHEMY_POOL_TIMEOUT: NonNegativeInt = Field(
        description="Number of seconds to wait for a pooled connection before raising a timeout.",
        default=30,
    )

    @property
    def SQLALCHEMY_ENGINE_OPTIONS(self) -> SQLAlchemyEngineOptionsDict:
        options = dict(parse_qsl(self.DB_EXTRAS)).get("options", "").strip()
        timezone = self.DB_SESSION_TIMEZONE_OVERRIDE.strip()
        if timezone:
            timezone_option = f"-c timezone={timezone}"
            options = f"{options} {timezone_option}" if options else timezone_option

        return {
            "pool_size": self.SQLALCHEMY_POOL_SIZE,
            "max_overflow": self.SQLALCHEMY_MAX_OVERFLOW,
            "pool_recycle": self.SQLALCHEMY_POOL_RECYCLE,
            "pool_pre_ping": self.SQLALCHEMY_POOL_PRE_PING,
            "connect_args": {"server_settings": {"options": options}} if options else {},
            "pool_use_lifo": self.SQLALCHEMY_POOL_USE_LIFO,
            "pool_timeout": self.SQLALCHEMY_POOL_TIMEOUT,
        }
