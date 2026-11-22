from pydantic import Field, PositiveFloat, PositiveInt
from pydantic_settings import BaseSettings


class AuthConfig(BaseSettings):
    ACCESS_TOKEN_EXPIRE_MINUTES: PositiveInt = Field(
        description="Expiration time for access tokens in minutes.",
        default=60,
    )
    REFRESH_TOKEN_EXPIRE_DAYS: PositiveFloat = Field(
        description="Expiration time for refresh tokens in days.",
        default=30,
    )
    LOGIN_LOCKOUT_DURATION: PositiveInt = Field(
        description="Time in seconds before login can be retried after exceeding the rate limit.",
        default=900,
    )
    AUTH_IP_LIMIT_MAX_ATTEMPTS: PositiveInt = Field(
        description="Maximum login requests allowed from one IP within the configured window.",
        default=20,
    )
    AUTH_IP_LIMIT_WINDOW_SECONDS: PositiveInt = Field(
        description="Sliding window in seconds for the per-IP login rate limit.",
        default=300,
    )
