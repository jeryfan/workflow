from typing import Annotated

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode


class HttpConfig(BaseSettings):
    PUBLIC_API_URL: str = Field(
        default="http://127.0.0.1:8000",
        description="Public base URL used in signed file URLs and cookie security inference.",
        validation_alias=AliasChoices("PUBLIC_API_URL", "CONSOLE_API_URL", "PUBLIC_ORIGIN"),
    )
    PUBLIC_WEB_URL: str = Field(
        default="http://localhost",
        description="Public web origin used in cookie security inference.",
        validation_alias=AliasChoices("PUBLIC_WEB_URL", "CONSOLE_WEB_URL", "PUBLIC_ORIGIN"),
    )
    CORS_ALLOWED_ORIGINS: Annotated[list[str], NoDecode] = Field(
        description="Comma-separated origins allowed to make cross-origin requests.",
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"],
    )
    CORS_ALLOW_CREDENTIALS: bool = Field(
        description="Whether cookies and Authorization headers are allowed in CORS requests.",
        default=True,
    )
    CORS_ALLOWED_METHODS: Annotated[list[str], NoDecode] = Field(
        description="List of HTTP methods allowed in CORS requests.",
        default_factory=lambda: ["*"],
    )
    CORS_ALLOWED_HEADERS: Annotated[list[str], NoDecode] = Field(
        description="List of HTTP request headers allowed in CORS requests.",
        default_factory=lambda: ["*"],
    )
    ALLOWED_HOSTS: Annotated[list[str], NoDecode] = Field(
        description="Comma-separated allowed Host header values. Use '*' to disable host validation.",
        default_factory=lambda: ["*"],
    )

    @field_validator(
        "CORS_ALLOWED_ORIGINS",
        "CORS_ALLOWED_METHODS",
        "CORS_ALLOWED_HEADERS",
        "ALLOWED_HOSTS",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.strip().split(",") if item.strip()]
        return value
