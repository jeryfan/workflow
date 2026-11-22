import hmac

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from api.constants import CONSOLE_API_PREFIX
from api.exceptions import CsrfValidationError
from api.exceptions.handlers import error_response
from api.libs.cookie import get_access_token, get_csrf_token_cookie, get_refresh_token
from api.libs.csrf import CSRF_TOKEN_HEADER, generate_csrf_token
from api.libs.origin import is_http_origin_allowed

EXEMPT_PATHS = {
    f"{CONSOLE_API_PREFIX}/setup",
    f"{CONSOLE_API_PREFIX}/login",
    f"{CONSOLE_API_PREFIX}/refresh-token",
}


class CsrfMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        try:
            self._verify(request, token_exempt=request.url.path in EXEMPT_PATHS)
        except CsrfValidationError as exc:
            response = error_response(
                status_code=exc.status_code,
                code=exc.code,
                message=exc.detail,
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)

    @staticmethod
    def _verify(request: Request, *, token_exempt: bool) -> None:
        if not is_http_origin_allowed(request):
            raise CsrfValidationError("Origin not allowed")

        if not get_access_token(request):
            return

        if token_exempt:
            return

        csrf_token_header = request.headers.get(CSRF_TOKEN_HEADER)
        csrf_token_cookie = get_csrf_token_cookie(request)
        if not csrf_token_header or not csrf_token_cookie:
            raise CsrfValidationError("Missing CSRF token")
        if not hmac.compare_digest(csrf_token_header.encode(), csrf_token_cookie.encode()):
            raise CsrfValidationError("CSRF token mismatch")

        refresh_token = get_refresh_token(request)
        if not refresh_token:
            raise CsrfValidationError("Missing refresh token")
        if not hmac.compare_digest(
            csrf_token_cookie.encode(),
            generate_csrf_token(refresh_token).encode(),
        ):
            raise CsrfValidationError("CSRF token not bound to session")
