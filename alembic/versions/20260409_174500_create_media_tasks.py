from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260409_174500"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("media_tasks"):
        op.create_table(
            "media_tasks",
            sa.Column("id", sa.String(length=255), primary_key=True),
            sa.Column("media_id", sa.String(length=255), nullable=False),
            sa.Column("device_id", sa.String(length=255), nullable=False),
            sa.Column("media_started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("cover_url", sa.Text(), nullable=True),
            sa.Column("encrypted_download_url", sa.Text(), nullable=True),
            sa.Column("source_day", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("feishu_record_id", sa.String(length=255), nullable=True),
            sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
            sa.Column("elimination_type", sa.String(length=32), nullable=True),
            sa.Column("stool_score", sa.String(length=32), nullable=True),
            sa.Column("stool_shape_note", sa.Text(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("raw_summary", sa.Text(), nullable=True),
        )
        inspector = sa.inspect(bind)

    existing_indexes = {index["name"] for index in inspector.get_indexes("media_tasks")}
    for index_name, columns in [
        ("ix_media_tasks_media_id", ["media_id"]),
        ("ix_media_tasks_device_id", ["device_id"]),
        ("ix_media_tasks_source_day", ["source_day"]),
        ("ix_media_tasks_status", ["status"]),
        ("ix_media_tasks_discovered_at", ["discovered_at"]),
        ("ix_media_tasks_updated_at", ["updated_at"]),
    ]:
        if index_name not in existing_indexes:
            op.create_index(index_name, "media_tasks", columns)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("media_tasks"):
        return

    existing_indexes = {index["name"] for index in inspector.get_indexes("media_tasks")}
    for index_name in [
        "ix_media_tasks_updated_at",
        "ix_media_tasks_discovered_at",
        "ix_media_tasks_status",
        "ix_media_tasks_source_day",
        "ix_media_tasks_device_id",
        "ix_media_tasks_media_id",
    ]:
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name="media_tasks")
    op.drop_table("media_tasks")
