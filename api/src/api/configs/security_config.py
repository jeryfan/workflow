from pydantic import Field
from pydantic_settings import BaseSettings


class SecurityConfig(BaseSettings):
    SECRET_KEY: str = Field(
        description="Secret key for JWT signing. Must be a strong random value of at least 32 characters.",
        min_length=32,
    )
    FILE_SIGNING_KEY: str = Field(
        description="Independent HMAC key for signed file URLs. Must contain at least 32 characters.",
        min_length=32,
    )
