from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies.auth import require_principal
from api.dependencies.db import get_db
from api.responses import error_response
from api.schemas.auth import Principal
from api.schemas.files import RemoteFilePayload
from api.services.files import DOCUMENT_EXTRACTOR_EXTENSIONS, FileError, FileService

management_router = APIRouter()
public_router = APIRouter()


async def upload_file(
    request: Request,
    db: AsyncSession,
    *,
    app_id: str | None,
    actor_id: str,
):
    form = await request.form()
    upload = form.get("file")
    if upload is None or isinstance(upload, str):
        return error_response(400, "no_file_uploaded", "A file is required")
    files = FileService(db)
    try:
        content = await upload.read(20 * 1024 * 1024 + 1)
        file = await files.save(
            app_id=app_id,
            actor_id=actor_id,
            filename=upload.filename or "",
            content=content,
            mime_type=upload.content_type,
        )
    except FileError as error:
        return error_response(400, "invalid_file", str(error))
    return JSONResponse(files.response(file), status_code=201)


@management_router.get("/files/support-type")
async def supported_document_types(_principal: Principal = Depends(require_principal)):
    return {"allowed_extensions": list(DOCUMENT_EXTRACTOR_EXTENSIONS)}


@management_router.post("/files/upload")
async def upload_account_file(
    request: Request,
    principal: Principal = Depends(require_principal),
    db: AsyncSession = Depends(get_db),
):
    return await upload_file(request, db, app_id=None, actor_id=principal.account_id)


@public_router.post("/files/upload")
async def upload_public_file(request: Request, db: AsyncSession = Depends(get_db)):
    from api.routers.public import public_workflow

    app, _site, _published = await public_workflow(request, db)
    return await upload_file(request, db, app_id=app.id, actor_id=f"webapp:{app.id}")


async def upload_remote(
    db: AsyncSession,
    *,
    app_id: str | None,
    actor_id: str,
    url: str,
):
    files = FileService(db)
    try:
        file = await files.fetch_remote(app_id=app_id, actor_id=actor_id, url=url)
    except FileError as error:
        return error_response(400, "invalid_remote_file", str(error))
    return files.response(file)


@management_router.post("/remote-files/upload")
async def upload_account_remote_file(
    payload: RemoteFilePayload,
    principal: Principal = Depends(require_principal),
    db: AsyncSession = Depends(get_db),
):
    return await upload_remote(db, app_id=None, actor_id=principal.account_id, url=payload.url)


@public_router.post("/remote-files/upload")
async def upload_public_remote_file(
    payload: RemoteFilePayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    from api.routers.public import public_workflow

    app, _site, _published = await public_workflow(request, db)
    return await upload_remote(db, app_id=app.id, actor_id=f"webapp:{app.id}", url=payload.url)


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


async def content_response(
    db: AsyncSession,
    file_id: str,
    expires: int,
    signature: str,
    as_attachment: bool,
):
    try:
        file, content = await FileService(db).content(file_id, expires=expires, signature=signature)
    except FileError as error:
        return error_response(403, "invalid_file_url", str(error))
    return file_content_response(file, content, as_attachment)


@public_router.get("/files/{file_id}/content")
async def public_file_content(
    file_id: str,
    expires: int,
    signature: str,
    as_attachment: bool = False,
    db: AsyncSession = Depends(get_db),
):
    return await content_response(db, file_id, expires, signature, as_attachment)


@management_router.get("/files/{file_id}/content")
async def authenticated_file_content(
    file_id: str,
    expires: int,
    signature: str,
    as_attachment: bool = False,
    _principal: Principal = Depends(require_principal),
    db: AsyncSession = Depends(get_db),
):
    return await content_response(db, file_id, expires, signature, as_attachment)


@management_router.get("/files/{file_id}/preview")
async def authenticated_file_preview(
    file_id: str,
    principal: Principal = Depends(require_principal),
    db: AsyncSession = Depends(get_db),
):
    try:
        content = await FileService(db).preview(file_id, account_id=principal.account_id)
    except FileError as error:
        return error_response(400, "invalid_file_preview", str(error))
    return {"content": content}


@public_router.get("/files/{file_id}/preview")
async def public_file_preview(
    file_id: str,
    expires: int,
    signature: str,
    db: AsyncSession = Depends(get_db),
):
    try:
        file, content = await FileService(db).content(file_id, expires=expires, signature=signature)
        if not (file.mime_type.startswith("text/") or file.extension in {".txt", ".md", ".json", ".csv"}):
            raise FileError("Only text file previews are supported")
    except FileError as error:
        return error_response(400, "invalid_file_preview", str(error))
    return {"content": content[:256_000].decode("utf-8", errors="replace")}
