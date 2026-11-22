from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"
    __table_args__ = (UniqueConstraint("app_id", "version_number"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    app_id: Mapped[str] = mapped_column(Text, ForeignKey("apps.id"))
    version_number: Mapped[int] = mapped_column(Integer)
    graph_json: Mapped[str] = mapped_column(Text)
    features_json: Mapped[str] = mapped_column(Text)
    environment_variables_json: Mapped[str] = mapped_column(Text)
    conversation_variables_json: Mapped[str] = mapped_column(Text)
    hash: Mapped[str] = mapped_column(Text)
    marked_name: Mapped[str] = mapped_column(Text)
    marked_comment: Mapped[str] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer)
    created_by_json: Mapped[str] = mapped_column(Text)


class WorkflowDraft(Base):
    __tablename__ = "workflow_drafts"

    app_id: Mapped[str] = mapped_column(Text, ForeignKey("apps.id"), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(Text, unique=True)
    graph_json: Mapped[str] = mapped_column(Text)
    features_json: Mapped[str] = mapped_column(Text)
    environment_variables_json: Mapped[str] = mapped_column(Text)
    conversation_variables_json: Mapped[str] = mapped_column(Text)
    hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[int] = mapped_column(Integer)
    created_by_json: Mapped[str] = mapped_column(Text)
    updated_by_json: Mapped[str] = mapped_column(Text)
