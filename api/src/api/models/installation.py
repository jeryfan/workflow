from sqlalchemy import CheckConstraint, Integer
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class InstallationState(Base):
    __tablename__ = "installation_state"
    __table_args__ = (CheckConstraint("id = 1", name="installation_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    setup_at: Mapped[int] = mapped_column(Integer)
