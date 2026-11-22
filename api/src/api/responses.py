from __future__ import annotations

from fastapi.responses import JSONResponse


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"code": code, "message": message, "status": status},
        headers={"Cache-Control": "no-store"},
    )
