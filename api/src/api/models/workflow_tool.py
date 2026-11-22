from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class WorkflowTool(Base):
    __tablename__ = "workflow_tools"
    __table_args__ = (UniqueConstraint("name"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    app_id: Mapped[str] = mapped_column(Text, ForeignKey("apps.id"), unique=True)
    published_workflow_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    icon_json: Mapped[str] = mapped_column(Text)
    parameters_json: Mapped[str] = mapped_column(Text)
    labels_json: Mapped[str] = mapped_column(Text)
    privacy_policy: Mapped[str] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[int] = mapped_column(Integer)
