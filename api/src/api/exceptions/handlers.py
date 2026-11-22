import logging
from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.configs import app_config

from . import BaseHTTPException

logger = logging.getLogger(__name__)

_STATUS_ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "file_too_large",
    422: "validation_error",
    429: "too_many_requests",
    500: "internal_server_error",
    503: "service_unavailable",
}


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    response_headers = {"Cache-Control": "no-store", **dict(headers or {})}
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "status": status_code},
        headers=response_headers,
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return error_response(
        status_code=422,
        code="validation_error",
        message="Validation error",
    )


async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    code = exc.code if isinstance(exc, BaseHTTPException) else _STATUS_ERROR_CODES.get(exc.status_code, "http_error")
    return error_response(
        status_code=exc.status_code,
        code=code,
        message=str(exc.detail),
        headers=exc.headers,
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled request exception")
    message = str(exc) or exc.__class__.__name__ if app_config.DEBUG else "Internal server error"
    return error_response(
        status_code=500,
        code="internal_server_error",
        message=message,
    )


def init_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(
        RequestValidationError,
        validation_exception_handler,
    )
    app.add_exception_handler(
        StarletteHTTPException,
        http_exception_handler,
    )
    app.add_exception_handler(Exception, generic_exception_handler)
