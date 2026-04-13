from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260413_170000"
down_revision = "20260413_150000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("media_tasks"):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("media_tasks")}
    with op.batch_alter_table("media_tasks") as batch_op:
        if "preview_path" not in existing_columns:
            batch_op.add_column(sa.Column("preview_path", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("media_tasks"):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("media_tasks")}
    with op.batch_alter_table("media_tasks") as batch_op:
        if "preview_path" in existing_columns:
            batch_op.drop_column("preview_path")
