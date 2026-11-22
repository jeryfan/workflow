from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "accounts",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("normalized_email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("interface_language", sa.Text(), nullable=False),
        sa.Column("interface_theme", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("initialized_at", sa.Integer(), nullable=True),
        sa.Column("last_login_at", sa.Integer(), nullable=True),
        sa.Column("last_login_ip", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_email"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="account_status"),
    )
    op.create_table(
        "workspace_memberships",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("workspace_id", "account_id"),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'editor', 'normal', 'dataset_operator')",
            name="workspace_role",
        ),
    )
    op.create_index(
        "one_workspace_owner",
        "workspace_memberships",
        ["workspace_id"],
        unique=True,
        sqlite_where=sa.text("role = 'owner'"),
    )
    op.create_table(
        "installation_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("setup_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="installation_singleton"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("access_token_hash", sa.Text(), nullable=False),
        sa.Column("refresh_token_hash", sa.Text(), nullable=False),
        sa.Column("csrf_token_hash", sa.Text(), nullable=False),
        sa.Column("access_expires_at", sa.Integer(), nullable=False),
        sa.Column("refresh_expires_at", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("refreshed_at", sa.Integer(), nullable=True),
        sa.Column("revoked_at", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("access_token_hash"),
        sa.UniqueConstraint("refresh_token_hash"),
    )
    op.create_index("auth_sessions_account_id", "auth_sessions", ["account_id"])
    op.create_table(
        "auth_throttles",
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("action", "subject"),
    )
    op.execute("ALTER TABLE apps ADD COLUMN maintainer_id TEXT REFERENCES accounts(id)")
    op.execute("ALTER TABLE apps ADD COLUMN created_by_id TEXT REFERENCES accounts(id)")
    op.execute("ALTER TABLE apps ADD COLUMN updated_by_id TEXT REFERENCES accounts(id)")
    op.create_table(
        "workflow_access_settings",
        sa.Column("app_id", sa.Text(), nullable=False),
        sa.Column("automatic_include_workspace_members", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"]),
        sa.PrimaryKeyConstraint("app_id"),
        sa.CheckConstraint("automatic_include_workspace_members IN (0, 1)", name="automatic_membership_flag"),
    )
    op.create_table(
        "workflow_acl",
        sa.Column("app_id", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("policy_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"]),
        sa.PrimaryKeyConstraint("app_id", "account_id"),
    )
    op.create_index("workflow_acl_account_id", "workflow_acl", ["account_id"])


def downgrade() -> None:
    raise NotImplementedError("Identity migration downgrade is not supported")
