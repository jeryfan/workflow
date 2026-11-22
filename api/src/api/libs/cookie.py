import logging
from urllib.parse import urlparse

from fastapi import Request, Response
from starlette.requests import HTTPConnection

from api.configs import app_config

logger = logging.getLogger(__name__)

_ACCESS_COOKIE_RAW = "access_token"
_REFRESH_COOKIE_RAW = "refresh_token"
_CSRF_COOKIE_RAW = "csrf_token"

_insecure_scheme_warned = False


def _is_secure(conn: HTTPConnection) -> bool:
    if app_config.COOKIE_SECURE is not None:
        return app_config.COOKIE_SECURE
    secure = urlparse(app_config.PUBLIC_API_URL).scheme.lower() == "https"
    secure = secure and urlparse(app_config.PUBLIC_WEB_URL).scheme.lower() == "https"
    if not secure and not app_config.DEBUG:
        global _insecure_scheme_warned
        if not _insecure_scheme_warned:
            _insecure_scheme_warned = True
            logger.warning(
                "Insecure cookie attributes inferred from public URLs; "
                "set COOKIE_SECURE explicitly or configure HTTPS public URLs"
            )
    return secure


def _host_prefix(conn: HTTPConnection) -> str:
    if _is_secure(conn) and not app_config.COOKIE_DOMAIN:
        return "__Host-"
    return ""


def access_cookie_name(request: Request) -> str:
    return f"{_host_prefix(request)}{_ACCESS_COOKIE_RAW}"


def csrf_token_cookie_name(request: Request) -> str:
    return f"{_host_prefix(request)}{_CSRF_COOKIE_RAW}"


def refresh_cookie_name(request: Request) -> str:
    return f"{_host_prefix(request)}{_REFRESH_COOKIE_RAW}"


def get_cookie(conn: HTTPConnection, raw_name: str) -> str | None:
    return conn.cookies.get(f"{_host_prefix(conn)}{raw_name}")


def get_access_token(conn: HTTPConnection) -> str | None:
    return get_cookie(conn, _ACCESS_COOKIE_RAW)


def get_refresh_token(conn: HTTPConnection) -> str | None:
    return get_cookie(conn, _REFRESH_COOKIE_RAW)


def get_csrf_token_cookie(conn: HTTPConnection) -> str | None:
    return get_cookie(conn, _CSRF_COOKIE_RAW)


def set_auth_cookies(
    response: Response,
    request: Request,
    *,
    access_token: str,
    refresh_token: str,
    csrf_token: str,
) -> None:
    secure = _is_secure(request)
    samesite = app_config.COOKIE_SAMESITE
    domain = app_config.COOKIE_DOMAIN
    access_max_age = app_config.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    refresh_max_age = int(app_config.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60)

    response.set_cookie(
        key=access_cookie_name(request),
        value=access_token,
        max_age=access_max_age,
        path="/",
        secure=secure,
        httponly=True,
        samesite=samesite,
        domain=domain,
    )
    response.set_cookie(
        key=refresh_cookie_name(request),
        value=refresh_token,
        max_age=refresh_max_age,
        path="/",
        secure=secure,
        httponly=True,
        samesite=samesite,
        domain=domain,
    )
    response.set_cookie(
        key=csrf_token_cookie_name(request),
        value=csrf_token,
        max_age=refresh_max_age,
        path="/",
        secure=secure,
        httponly=False,
        samesite=samesite,
        domain=domain,
    )


def clear_auth_cookies(response: Response, request: Request) -> None:
    secure = _is_secure(request)
    domain = app_config.COOKIE_DOMAIN
    for name, httponly in (
        (access_cookie_name(request), True),
        (refresh_cookie_name(request), True),
        (csrf_token_cookie_name(request), False),
    ):
        response.delete_cookie(
            key=name,
            path="/",
            domain=domain,
            secure=secure,
            httponly=httponly,
            samesite=app_config.COOKIE_SAMESITE,
        )
