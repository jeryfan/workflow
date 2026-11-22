from sqlalchemy import ForeignKey, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class App(Base):
    __tablename__ = "apps"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer)
    maintainer_id: Mapped[str | None] = mapped_column(Text, ForeignKey("accounts.id"))
    created_by_id: Mapped[str | None] = mapped_column(Text, ForeignKey("accounts.id"))
    updated_by_id: Mapped[str | None] = mapped_column(Text, ForeignKey("accounts.id"))
    published_workflow_id: Mapped[str | None] = mapped_column(Text, ForeignKey("workflow_versions.id"))
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    icon: Mapped[str | None] = mapped_column(Text)
    icon_type: Mapped[str | None] = mapped_column(Text)
    icon_background: Mapped[str | None] = mapped_column(Text)
    use_icon_as_answer_icon: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    updated_at: Mapped[int | None] = mapped_column(Integer)


class AppSite(Base):
    __tablename__ = "app_sites"

    app_id: Mapped[str] = mapped_column(Text, ForeignKey("apps.id"), primary_key=True)
    access_token: Mapped[str] = mapped_column(Text, unique=True)
    enable_site: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[int] = mapped_column(Integer)
