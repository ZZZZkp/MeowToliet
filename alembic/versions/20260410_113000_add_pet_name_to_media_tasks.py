from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260410_113000"
down_revision = "20260409_174500"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("media_tasks"):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("media_tasks")}
    if "pet_name" in existing_columns:
        return

    with op.batch_alter_table("media_tasks") as batch_op:
        batch_op.add_column(sa.Column("pet_name", sa.String(length=255), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("media_tasks"):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("media_tasks")}
    if "pet_name" not in existing_columns:
        return

    with op.batch_alter_table("media_tasks") as batch_op:
        batch_op.drop_column("pet_name")
