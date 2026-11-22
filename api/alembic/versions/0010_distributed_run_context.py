from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflow_runs", sa.Column("root_node_id", sa.Text(), nullable=True))
    op.add_column("workflow_runs", sa.Column("call_depth", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("workflow_runs", sa.Column("call_stack_json", sa.Text(), nullable=False, server_default="[]"))


def downgrade() -> None:
    raise NotImplementedError("Distributed run context downgrade is not supported")
