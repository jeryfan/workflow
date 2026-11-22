from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import Request, Response

from api.services.auth import ACCESS_TOKEN_TTL, REFRESH_TOKEN_TTL, IssuedSession


@dataclass(frozen=True, slots=True)
class CookiePolicy:
    secure: bool = False
    domain: str | None = None

    @classmethod
    def from_urls(cls, api_url: str, web_url: str, domain: str | None = None) -> CookiePolicy:
        secure = urlparse(api_url).scheme.lower() == "https"
        secure = secure and urlparse(web_url).scheme.lower() == "https"
        normalized_domain = domain.strip().lstrip(".").rstrip(".").lower() if domain else None
        return cls(secure=secure, domain=normalized_domain or None)

    @classmethod
    def from_environment(cls) -> CookiePolicy:
        return cls.from_urls(
            os.environ.get("CONSOLE_API_URL", "http://127.0.0.1:5001"),
            os.environ.get("CONSOLE_WEB_URL", "http://127.0.0.1:3000"),
            os.environ.get("COOKIE_DOMAIN"),
        )

    def access_token(self, request: Request) -> str | None:
        return request.cookies.get(self._name("access_token"))

    def refresh_token(self, request: Request) -> str | None:
        return request.cookies.get(self._name("refresh_token"))

    def csrf_token(self, request: Request) -> str | None:
        return request.cookies.get(self._name("csrf_token"))

    def set_session(self, response: Response, issued: IssuedSession) -> None:
        self._set_cookie(response, "access_token", issued.access_token, ACCESS_TOKEN_TTL, http_only=True)
        self._set_cookie(response, "refresh_token", issued.refresh_token, REFRESH_TOKEN_TTL, http_only=True)
        self._set_cookie(response, "csrf_token", issued.csrf_token, ACCESS_TOKEN_TTL, http_only=False)

    def clear_session(self, response: Response) -> None:
        for name, http_only in (("access_token", True), ("refresh_token", True), ("csrf_token", False)):
            self._set_cookie(response, name, "", 0, http_only=http_only, expires=0)

    def _name(self, base_name: str) -> str:
        return f"__Host-{base_name}" if self.secure and self.domain is None else base_name

    def _set_cookie(
        self,
        response: Response,
        base_name: str,
        value: str,
        max_age: int,
        http_only: bool,
        expires: int | None = None,
    ) -> None:
        response.set_cookie(
            key=self._name(base_name),
            value=value,
            max_age=max_age,
            expires=expires,
            path="/",
            domain=self.domain,
            secure=self.secure,
            httponly=http_only,
            samesite="lax",
        )


def cookie_policy(request: Request) -> CookiePolicy:
    configured = getattr(request.app.state, "cookies", None)
    return configured if isinstance(configured, CookiePolicy) else CookiePolicy.from_environment()
