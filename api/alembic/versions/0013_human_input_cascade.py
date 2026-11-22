from __future__ import annotations

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    naming_convention = {"fk": "%(table_name)s_%(column_0_name)s_fkey"}
    with op.batch_alter_table("human_input_forms", naming_convention=naming_convention) as batch:
        batch.drop_constraint("human_input_forms_workflow_run_id_fkey", type_="foreignkey")
        batch.create_foreign_key(
            "human_input_forms_workflow_run_id_fkey",
            "workflow_runs",
            ["workflow_run_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    raise NotImplementedError("Human input cascade downgrade is not supported")
