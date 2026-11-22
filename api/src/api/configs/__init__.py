import os
from pathlib import Path
from typing import Any, cast

from pydantic import model_validator
from pydantic_settings import SettingsConfigDict

from .auth_config import AuthConfig
from .celery_config import CeleryConfig
from .cookie_config import CookieConfig
from .db_config import DatabaseConfig
from .deployment_config import DeploymentConfig
from .http_config import HttpConfig
from .llm_config import LLMConfig
from .log_config import LoggingConfig
from .security_config import SecurityConfig
from .storage import StorageConfig

PROJECT_ROOT = Path(os.environ.get("WORKFLOW_PROJECT_ROOT", Path(__file__).resolve().parents[4]))
_EMPTY_STRING_MEANINGFUL_FIELDS = {"DB_SESSION_TIMEZONE_OVERRIDE"}


class AppConfig(
    CeleryConfig,
    DatabaseConfig,
    DeploymentConfig,
    HttpConfig,
    LoggingConfig,
    AuthConfig,
    SecurityConfig,
    CookieConfig,
    StorageConfig,
    LLMConfig,
):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="before")
    @classmethod
    def _empty_string_to_unset(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {
                key: value
                for key, value in cast(dict[str, Any], data).items()
                if value != "" or key in _EMPTY_STRING_MEANINGFUL_FIELDS
            }
        return data


app_config = AppConfig()
