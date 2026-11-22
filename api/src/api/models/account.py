from sqlalchemy import CheckConstraint, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (CheckConstraint("status IN ('active', 'disabled')", name="account_status"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    email: Mapped[str] = mapped_column(Text)
    normalized_email: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    interface_language: Mapped[str] = mapped_column(Text)
    interface_theme: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    initialized_at: Mapped[int | None] = mapped_column(Integer)
    last_login_at: Mapped[int | None] = mapped_column(Integer)
    last_login_ip: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(Text)
