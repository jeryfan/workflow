from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("app_id", sa.Text(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("graph_json", sa.Text(), nullable=False),
        sa.Column("features_json", sa.Text(), nullable=False),
        sa.Column("environment_variables_json", sa.Text(), nullable=False),
        sa.Column("conversation_variables_json", sa.Text(), nullable=False),
        sa.Column("hash", sa.Text(), nullable=False),
        sa.Column("marked_name", sa.Text(), nullable=False),
        sa.Column("marked_comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("created_by_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("app_id", "version_number"),
    )
    op.execute("ALTER TABLE apps ADD COLUMN published_workflow_id TEXT REFERENCES workflow_versions(id)")


def downgrade() -> None:
    raise NotImplementedError("Workflow publication downgrade is not supported")
