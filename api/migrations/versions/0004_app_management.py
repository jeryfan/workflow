from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE apps ADD COLUMN description TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE apps ADD COLUMN icon TEXT")
    op.execute("ALTER TABLE apps ADD COLUMN icon_type TEXT")
    op.execute("ALTER TABLE apps ADD COLUMN icon_background TEXT")
    op.execute("ALTER TABLE apps ADD COLUMN use_icon_as_answer_icon INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE apps ADD COLUMN updated_at INTEGER")
    op.execute("UPDATE apps SET updated_at = created_at WHERE updated_at IS NULL")
    op.execute("UPDATE apps SET icon = '🤖', icon_type = 'emoji', icon_background = '#FFEAD5' WHERE icon IS NULL")


def downgrade() -> None:
    raise NotImplementedError("App management downgrade is not supported")
