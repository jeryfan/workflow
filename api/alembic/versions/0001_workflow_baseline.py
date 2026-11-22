from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "apps",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "workflow_drafts",
        sa.Column("app_id", sa.Text(), nullable=False),
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("graph_json", sa.Text(), nullable=False),
        sa.Column("features_json", sa.Text(), nullable=False),
        sa.Column("environment_variables_json", sa.Text(), nullable=False),
        sa.Column("conversation_variables_json", sa.Text(), nullable=False),
        sa.Column("hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.Column("created_by_json", sa.Text(), nullable=False),
        sa.Column("updated_by_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"]),
        sa.PrimaryKeyConstraint("app_id"),
        sa.UniqueConstraint("workflow_id"),
    )
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("app_id", sa.Text(), nullable=False),
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("draft_hash", sa.Text(), nullable=False),
        sa.Column("graph_snapshot_json", sa.Text(), nullable=False),
        sa.Column("inputs_json", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("outputs_json", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("elapsed_time", sa.Float(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("total_steps", sa.Integer(), nullable=False),
        sa.Column("exceptions_count", sa.Integer(), nullable=False),
        sa.Column("created_by_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("finished_at", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_table(
        "node_executions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("node_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("predecessor_node_id", sa.Text(), nullable=True),
        sa.Column("inputs_json", sa.Text(), nullable=False),
        sa.Column("process_data_json", sa.Text(), nullable=False),
        sa.Column("outputs_json", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("elapsed_time", sa.Float(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("finished_at", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence"),
    )


def downgrade() -> None:
    raise NotImplementedError("Workflow baseline downgrade is not supported")
