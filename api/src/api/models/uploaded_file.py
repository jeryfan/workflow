from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    app_id: Mapped[str | None] = mapped_column(Text, ForeignKey("apps.id"))
    name: Mapped[str] = mapped_column(Text)
    extension: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer)
