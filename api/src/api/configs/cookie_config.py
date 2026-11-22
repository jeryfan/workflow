from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class CookieConfig(BaseSettings):
    COOKIE_DOMAIN: str | None = Field(
        default=None,
        description="Optional cookie Domain attribute. Leave empty for host-only cookies.",
    )
    COOKIE_SECURE: bool | None = Field(
        default=None,
        description="Force the Secure flag on auth cookies. None infers it from the public URLs.",
    )
    COOKIE_SAMESITE: Literal["lax", "strict", "none"] = Field(
        default="lax",
        description="SameSite attribute for auth cookies.",
    )
