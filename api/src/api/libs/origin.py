from urllib.parse import urlparse

from fastapi import Request
from starlette import status
from starlette.exceptions import WebSocketException
from starlette.websockets import WebSocket

from api.configs import app_config

_ORIGIN_WILDCARD = "*"


def _origin_matches_allowlist(origin: str, host: str) -> bool:
    if host and urlparse(origin).netloc == host:
        return True
    allowlist = app_config.CORS_ALLOWED_ORIGINS
    return _ORIGIN_WILDCARD in allowlist or origin in allowlist


def is_http_origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    return _origin_matches_allowlist(origin, request.headers.get("host", ""))


def validate_websocket_origin(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    if not origin:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Missing Origin",
        )

    if _origin_matches_allowlist(origin, websocket.headers.get("host", "")):
        return

    raise WebSocketException(
        code=status.WS_1008_POLICY_VIOLATION,
        reason="Origin not allowed",
    )
