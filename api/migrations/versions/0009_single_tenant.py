from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("role", sa.Text(), nullable=False, server_default="normal"))
    op.execute(
        """
        UPDATE accounts
        SET role = (
            SELECT workspace_memberships.role
            FROM workspace_memberships
            WHERE workspace_memberships.account_id = accounts.id
        )
        WHERE EXISTS (
            SELECT 1 FROM workspace_memberships WHERE workspace_memberships.account_id = accounts.id
        )
        """
    )
    with op.batch_alter_table("accounts") as batch:
        batch.alter_column("role", existing_type=sa.Text(), nullable=False, server_default=None)

    op.rename_table("workflow_tools", "_workflow_tools_legacy")
    op.create_table(
        "workflow_tools",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("app_id", sa.Text(), nullable=False),
        sa.Column("published_workflow_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("icon_json", sa.Text(), nullable=False),
        sa.Column("parameters_json", sa.Text(), nullable=False),
        sa.Column("labels_json", sa.Text(), nullable=False),
        sa.Column("privacy_policy", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("app_id"),
        sa.UniqueConstraint("name"),
    )
    op.execute(
        """
        INSERT INTO workflow_tools (
            id, app_id, published_workflow_id, name, label, description, icon_json,
            parameters_json, labels_json, privacy_policy, created_at, updated_at
        )
        SELECT id, app_id, published_workflow_id, name, label, description, icon_json,
               parameters_json, labels_json, privacy_policy, created_at, updated_at
        FROM _workflow_tools_legacy
        """
    )
    op.drop_table("_workflow_tools_legacy")

    for table, column in (
        ("apps", "workspace_id"),
        ("uploaded_files", "workspace_id"),
        ("installation_state", "workspace_id"),
    ):
        with op.batch_alter_table(table) as batch:
            batch.drop_column(column)

    op.drop_table("workflow_acl")
    op.drop_table("workflow_access_settings")
    op.drop_table("workspace_memberships")
    op.drop_table("workspaces")


def downgrade() -> None:
    raise NotImplementedError("Single-tenant migration downgrade is not supported")
