from sqlalchemy import ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class HumanInputForm(Base):
    __tablename__ = "human_input_forms"
    __table_args__ = (
        Index("human_input_forms_run_node_idx", "workflow_run_id", "node_id"),
        Index("human_input_forms_status_expiration_idx", "status", "expiration_time"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    access_token: Mapped[str] = mapped_column(Text, unique=True)
    app_id: Mapped[str] = mapped_column(Text, ForeignKey("apps.id"))
    workflow_run_id: Mapped[str] = mapped_column(Text, ForeignKey("workflow_runs.id", ondelete="CASCADE"))
    node_id: Mapped[str] = mapped_column(Text)
    node_execution_id: Mapped[str] = mapped_column(Text)
    created_by_id: Mapped[str] = mapped_column(Text)
    definition_json: Mapped[str] = mapped_column(Text)
    rendered_content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    expiration_time: Mapped[int] = mapped_column(Integer)
    selected_action_id: Mapped[str | None] = mapped_column(Text)
    submitted_data_json: Mapped[str | None] = mapped_column(Text)
    submitted_by_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[int] = mapped_column(Integer)
    submitted_at: Mapped[int | None] = mapped_column(Integer)
