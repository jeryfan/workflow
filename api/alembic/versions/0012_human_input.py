from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflow_runs", sa.Column("runtime_state_json", sa.Text(), nullable=True))
    op.create_table(
        "human_input_forms",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("app_id", sa.Text(), nullable=False),
        sa.Column("workflow_run_id", sa.Text(), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("node_execution_id", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Text(), nullable=False),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("rendered_content", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("expiration_time", sa.Integer(), nullable=False),
        sa.Column("selected_action_id", sa.Text(), nullable=True),
        sa.Column("submitted_data_json", sa.Text(), nullable=True),
        sa.Column("submitted_by_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.Column("submitted_at", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"]),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("access_token"),
    )
    op.create_index(
        "human_input_forms_run_node_idx",
        "human_input_forms",
        ["workflow_run_id", "node_id"],
    )
    op.create_index(
        "human_input_forms_status_expiration_idx",
        "human_input_forms",
        ["status", "expiration_time"],
    )


def downgrade() -> None:
    raise NotImplementedError("Human input downgrade is not supported")
