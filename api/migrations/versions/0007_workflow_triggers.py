from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_triggers",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("app_id", sa.Text(), nullable=False),
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("trigger_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("webhook_id", sa.Text(), nullable=True),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("created_by_json", sa.Text(), nullable=False),
        sa.Column("next_run_at", sa.Integer(), nullable=True),
        sa.Column("last_run_key", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("app_id", "node_id"),
        sa.UniqueConstraint("webhook_id"),
    )
    op.create_index(
        "ix_workflow_triggers_schedule_poll",
        "workflow_triggers",
        ["trigger_type", "status", "next_run_at"],
        unique=False,
    )


def downgrade() -> None:
    raise NotImplementedError("Workflow trigger downgrade is not supported")
