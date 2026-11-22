from __future__ import annotations

import os
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(os.environ.get("WORKFLOW_PROJECT_ROOT", str(Path(__file__).resolve().parents[3]))).resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = f"sqlite:///{PROJECT_ROOT / '.data' / 'workflow.db'}"
    redis_url: str = "redis://127.0.0.1:6379/0"
    execution_backend: Literal["local", "celery"] = "local"
    public_api_url: str = Field(
        default="http://127.0.0.1:5001",
        validation_alias=AliasChoices("PUBLIC_API_URL", "CONSOLE_API_URL"),
    )
    public_web_url: str = Field(
        default="http://127.0.0.1:3000",
        validation_alias=AliasChoices("PUBLIC_WEB_URL", "CONSOLE_WEB_URL"),
    )
    file_signing_key: str | None = None
    storage_backend: Literal["local", "s3"] = "local"
    s3_bucket: str = "workflow"
    s3_region: str = "us-east-1"
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    task_time_limit_seconds: int = Field(default=3600, ge=60)
    task_soft_time_limit_seconds: int = Field(default=3500, ge=30)


@cache
def get_settings() -> Settings:
    return Settings()
