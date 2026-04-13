from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260413_110000"
down_revision = "20260410_113000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("media_tasks"):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("media_tasks")}
    with op.batch_alter_table("media_tasks") as batch_op:
        if "last_error_kind" not in existing_columns:
            batch_op.add_column(sa.Column("last_error_kind", sa.String(length=64), nullable=True))
        if "next_attempt_at" not in existing_columns:
            batch_op.add_column(sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
        if "last_attempt_started_at" not in existing_columns:
            batch_op.add_column(
                sa.Column("last_attempt_started_at", sa.DateTime(timezone=True), nullable=True),
            )

    inspector = sa.inspect(bind)
    existing_indexes = {index["name"] for index in inspector.get_indexes("media_tasks")}
    if "ix_media_tasks_next_attempt_at" not in existing_indexes:
        op.create_index("ix_media_tasks_next_attempt_at", "media_tasks", ["next_attempt_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("media_tasks"):
        return

    existing_indexes = {index["name"] for index in inspector.get_indexes("media_tasks")}
    if "ix_media_tasks_next_attempt_at" in existing_indexes:
        op.drop_index("ix_media_tasks_next_attempt_at", table_name="media_tasks")

    existing_columns = {column["name"] for column in inspector.get_columns("media_tasks")}
    with op.batch_alter_table("media_tasks") as batch_op:
        if "last_attempt_started_at" in existing_columns:
            batch_op.drop_column("last_attempt_started_at")
        if "next_attempt_at" in existing_columns:
            batch_op.drop_column("next_attempt_at")
        if "last_error_kind" in existing_columns:
            batch_op.drop_column("last_error_kind")
