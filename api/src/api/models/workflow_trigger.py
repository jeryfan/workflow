from sqlalchemy import ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class WorkflowTrigger(Base):
    __tablename__ = "workflow_triggers"
    __table_args__ = (
        UniqueConstraint("app_id", "node_id"),
        Index("ix_workflow_triggers_schedule_poll", "trigger_type", "status", "next_run_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    app_id: Mapped[str] = mapped_column(Text, ForeignKey("apps.id"))
    workflow_id: Mapped[str] = mapped_column(Text)
    node_id: Mapped[str] = mapped_column(Text)
    trigger_type: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    webhook_id: Mapped[str | None] = mapped_column(Text, unique=True)
    config_json: Mapped[str] = mapped_column(Text)
    created_by_json: Mapped[str] = mapped_column(Text)
    next_run_at: Mapped[int | None] = mapped_column(Integer)
    last_run_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[int] = mapped_column(Integer)
