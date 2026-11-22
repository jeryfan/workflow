from sqlalchemy import Float, ForeignKey, Integer, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, unique=True)
    app_id: Mapped[str] = mapped_column(Text, ForeignKey("apps.id"))
    workflow_id: Mapped[str] = mapped_column(Text)
    draft_hash: Mapped[str] = mapped_column(Text)
    graph_snapshot_json: Mapped[str] = mapped_column(Text)
    inputs_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    outputs_json: Mapped[str] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    elapsed_time: Mapped[float] = mapped_column(Float)
    total_tokens: Mapped[int] = mapped_column(Integer)
    total_steps: Mapped[int] = mapped_column(Integer)
    exceptions_count: Mapped[int] = mapped_column(Integer)
    created_by_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer)
    finished_at: Mapped[int | None] = mapped_column(Integer)
    root_node_id: Mapped[str | None] = mapped_column(Text)
    call_depth: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    call_stack_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    runtime_state_json: Mapped[str | None] = mapped_column(Text)


class NodeExecution(Base):
    __tablename__ = "node_executions"
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(Text, ForeignKey("workflow_runs.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    node_id: Mapped[str] = mapped_column(Text)
    node_type: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    index: Mapped[int] = mapped_column(Integer)
    predecessor_node_id: Mapped[str | None] = mapped_column(Text)
    inputs_json: Mapped[str] = mapped_column(Text)
    process_data_json: Mapped[str] = mapped_column(Text)
    outputs_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    elapsed_time: Mapped[float] = mapped_column(Float)
    created_at: Mapped[int] = mapped_column(Integer)
    finished_at: Mapped[int | None] = mapped_column(Integer)
