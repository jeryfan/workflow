from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class DeploymentConfig(BaseSettings):
    APP_ENV: Literal["development", "test", "production"] = Field(
        description="Application deployment environment.",
        default="development",
    )
    DEBUG: bool = Field(
        description="Enable debug mode for additional logging and development features.",
        default=False,
    )
    EXECUTION_BACKEND: Literal["local", "celery"] = Field(
        description="Workflow execution backend.",
        default="local",
    )
