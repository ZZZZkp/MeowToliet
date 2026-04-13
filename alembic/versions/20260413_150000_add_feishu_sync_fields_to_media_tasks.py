from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260413_150000"
down_revision = "20260413_110000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("media_tasks"):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("media_tasks")}
    with op.batch_alter_table("media_tasks") as batch_op:
        if "screenshot_path" not in existing_columns:
            batch_op.add_column(sa.Column("screenshot_path", sa.Text(), nullable=True))
        if "feishu_sync_status" not in existing_columns:
            batch_op.add_column(sa.Column("feishu_sync_status", sa.String(length=32), nullable=True))
        if "feishu_sync_attempts" not in existing_columns:
            batch_op.add_column(
                sa.Column("feishu_sync_attempts", sa.Integer(), nullable=False, server_default="0"),
            )
        if "feishu_sync_last_error" not in existing_columns:
            batch_op.add_column(sa.Column("feishu_sync_last_error", sa.Text(), nullable=True))
        if "feishu_sync_last_error_kind" not in existing_columns:
            batch_op.add_column(
                sa.Column("feishu_sync_last_error_kind", sa.String(length=64), nullable=True),
            )
        if "feishu_sync_next_attempt_at" not in existing_columns:
            batch_op.add_column(
                sa.Column("feishu_sync_next_attempt_at", sa.DateTime(timezone=True), nullable=True),
            )
        if "feishu_synced_at" not in existing_columns:
            batch_op.add_column(sa.Column("feishu_synced_at", sa.DateTime(timezone=True), nullable=True))

    inspector = sa.inspect(bind)
    existing_indexes = {index["name"] for index in inspector.get_indexes("media_tasks")}
    if "ix_media_tasks_feishu_sync_status" not in existing_indexes:
        op.create_index("ix_media_tasks_feishu_sync_status", "media_tasks", ["feishu_sync_status"])
    if "ix_media_tasks_feishu_sync_next_attempt_at" not in existing_indexes:
        op.create_index(
            "ix_media_tasks_feishu_sync_next_attempt_at",
            "media_tasks",
            ["feishu_sync_next_attempt_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("media_tasks"):
        return

    existing_indexes = {index["name"] for index in inspector.get_indexes("media_tasks")}
    if "ix_media_tasks_feishu_sync_next_attempt_at" in existing_indexes:
        op.drop_index("ix_media_tasks_feishu_sync_next_attempt_at", table_name="media_tasks")
    if "ix_media_tasks_feishu_sync_status" in existing_indexes:
        op.drop_index("ix_media_tasks_feishu_sync_status", table_name="media_tasks")

    existing_columns = {column["name"] for column in inspector.get_columns("media_tasks")}
    with op.batch_alter_table("media_tasks") as batch_op:
        if "feishu_synced_at" in existing_columns:
            batch_op.drop_column("feishu_synced_at")
        if "feishu_sync_next_attempt_at" in existing_columns:
            batch_op.drop_column("feishu_sync_next_attempt_at")
        if "feishu_sync_last_error_kind" in existing_columns:
            batch_op.drop_column("feishu_sync_last_error_kind")
        if "feishu_sync_last_error" in existing_columns:
            batch_op.drop_column("feishu_sync_last_error")
        if "feishu_sync_attempts" in existing_columns:
            batch_op.drop_column("feishu_sync_attempts")
        if "feishu_sync_status" in existing_columns:
            batch_op.drop_column("feishu_sync_status")
        if "screenshot_path" in existing_columns:
            batch_op.drop_column("screenshot_path")
