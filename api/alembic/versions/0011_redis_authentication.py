from __future__ import annotations

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("auth_sessions")
    op.drop_table("auth_throttles")


def downgrade() -> None:
    raise NotImplementedError("Redis authentication downgrade is not supported")
