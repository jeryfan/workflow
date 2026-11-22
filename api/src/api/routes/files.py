from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from api.dependencies.auth import require_principal
from api.responses import error_response
from api.routes.public import public_workflow
from api.schemas.files import RemoteFilePayload
from api.services.files import FileError, FileService

management_router = APIRouter()
public_router = APIRouter()


def service(request: Request) -> FileService:
    return request.app.state.files


async def upload_file(request: Request, *, app_id: str | None, actor_id: str):
    form = await request.form()
    upload = form.get("file")
    if upload is None or isinstance(upload, str):
        return error_response(400, "no_file_uploaded", "A file is required")
    try:
        content = await upload.read(20 * 1024 * 1024 + 1)
        file = service(request).save(
            app_id=app_id,
            actor_id=actor_id,
            filename=upload.filename or "",
            content=content,
            mime_type=upload.content_type,
        )
    except FileError as error:
        return error_response(400, "invalid_file", str(error))
    return JSONResponse(service(request).response(file), status_code=201)


@management_router.post("/files/upload")
async def upload_account_file(request: Request):
    principal = require_principal(request)
    return await upload_file(
        request,
        app_id=None,
        actor_id=principal.account_id,
    )


@public_router.post("/files/upload")
async def upload_public_file(request: Request):
    app, _site, _published = public_workflow(request)
    return await upload_file(
        request,
        app_id=app.id,
        actor_id=f"webapp:{app.id}",
    )


async def upload_remote(request: Request, *, app_id: str | None, actor_id: str, url: str):
    try:
        file = service(request).fetch_remote(
            app_id=app_id,
            actor_id=actor_id,
            url=url,
        )
    except FileError as error:
        return error_response(400, "invalid_remote_file", str(error))
    return service(request).response(file)


@management_router.post("/remote-files/upload")
async def upload_account_remote_file(request: Request, payload: RemoteFilePayload):
    principal = require_principal(request)
    return await upload_remote(
        request,
        app_id=None,
        actor_id=principal.account_id,
        url=payload.url,
    )


@public_router.post("/remote-files/upload")
async def upload_public_remote_file(request: Request, payload: RemoteFilePayload):
    app, _site, _published = public_workflow(request)
    return await upload_remote(
        request,
        app_id=app.id,
        actor_id=f"webapp:{app.id}",
        url=payload.url,
    )


def file_content_response(file, content: bytes, as_attachment: bool = False) -> Response:
    inline_image = file.mime_type.startswith("image/") and file.extension != ".svg"
    disposition = "attachment" if as_attachment or not inline_image else "inline"
    return Response(
        content=content,
        media_type=file.mime_type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(file.name)}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def content_response(request: Request, file_id: str, expires: int, signature: str, as_attachment: bool):
    try:
        file, content = service(request).content(file_id, expires=expires, signature=signature)
    except FileError as error:
        return error_response(403, "invalid_file_url", str(error))
    return file_content_response(file, content, as_attachment)


@public_router.get("/files/{file_id}/content")
def public_file_content(request: Request, file_id: str, expires: int, signature: str, as_attachment: bool = False):
    return content_response(request, file_id, expires, signature, as_attachment)


@management_router.get("/files/{file_id}/content")
def authenticated_file_content(
    request: Request, file_id: str, expires: int, signature: str, as_attachment: bool = False
):
    require_principal(request)
    return content_response(request, file_id, expires, signature, as_attachment)


@management_router.get("/files/{file_id}/preview")
def authenticated_file_preview(request: Request, file_id: str):
    principal = require_principal(request)
    try:
        content = service(request).preview(file_id, account_id=principal.account_id)
    except FileError as error:
        return error_response(400, "invalid_file_preview", str(error))
    return {"content": content}


@public_router.get("/files/{file_id}/preview")
def public_file_preview(request: Request, file_id: str, expires: int, signature: str):
    try:
        file, content = service(request).content(file_id, expires=expires, signature=signature)
        if not (file.mime_type.startswith("text/") or file.extension in {".txt", ".md", ".json", ".csv"}):
            raise FileError("Only text file previews are supported")
    except FileError as error:
        return error_response(400, "invalid_file_preview", str(error))
    return {"content": content[:256_000].decode("utf-8", errors="replace")}
