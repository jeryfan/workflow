from sqlalchemy.ext.asyncio import AsyncSession

from .files import FileService


def create_file_service(db: AsyncSession) -> FileService:
    return FileService(db)
