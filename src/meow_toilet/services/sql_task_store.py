from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker

from meow_toilet.domain.entities import (
    EliminationType,
    JobStatus,
    MediaTask,
    PetKitMedia,
    PipelineOutcome,
    StaleRecoveryResult,
    SyncStatus,
)

Base = declarative_base()


class MediaTaskRecord(Base):
    __tablename__ = "media_tasks"

    id = Column(String(255), primary_key=True)
    media_id = Column(String(255), nullable=False, index=True)
    device_id = Column(String(255), nullable=False, index=True)
    media_started_at = Column(DateTime(timezone=True), nullable=False)
    cover_url = Column(Text(), nullable=True)
    encrypted_download_url = Column(Text(), nullable=True)
    source_day = Column(String(32), nullable=False, index=True)
    pet_name = Column(String(255), nullable=True)
    preview_path = Column(Text(), nullable=True)
    status = Column(String(32), nullable=False, index=True)
    discovered_at = Column(DateTime(timezone=True), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, index=True)
    attempts = Column(Integer(), nullable=False, default=0)
    last_error = Column(Text(), nullable=True)
    last_error_kind = Column(String(64), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_attempt_started_at = Column(DateTime(timezone=True), nullable=True)
    screenshot_path = Column(Text(), nullable=True)
    feishu_record_id = Column(String(255), nullable=True)
    feishu_sync_status = Column(String(32), nullable=True, index=True)
    feishu_sync_attempts = Column(Integer(), nullable=False, default=0)
    feishu_sync_last_error = Column(Text(), nullable=True)
    feishu_sync_last_error_kind = Column(String(64), nullable=True)
    feishu_sync_next_attempt_at = Column(DateTime(timezone=True), nullable=True, index=True)
    feishu_synced_at = Column(DateTime(timezone=True), nullable=True)
    event_time = Column(DateTime(timezone=True), nullable=True)
    elimination_type = Column(String(32), nullable=True)
    stool_score = Column(String(32), nullable=True)
    stool_shape_note = Column(Text(), nullable=True)
    confidence = Column(Float(), nullable=True)
    raw_summary = Column(Text(), nullable=True)


class SqlAlchemyMediaTaskStore:
    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url, future=True)
        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False)
        Base.metadata.create_all(self._engine)
        self._ensure_compatible_schema()

    async def enqueue_media(
        self,
        media: PetKitMedia,
        discovered_at: datetime,
        *,
        preview_path: Path | None = None,
    ) -> tuple[MediaTask, bool]:
        try:
            with self._session_factory.begin() as session:
                record = MediaTaskRecord(
                    id=media.dedupe_key,
                    media_id=media.id,
                    device_id=media.device_id,
                    media_started_at=media.started_at,
                    cover_url=media.cover_url,
                    encrypted_download_url=media.encrypted_download_url,
                    source_day=media.source_day,
                    pet_name=media.pet_name,
                    preview_path=None if preview_path is None else str(preview_path),
                    status=JobStatus.QUEUED.value,
                    discovered_at=discovered_at,
                    updated_at=discovered_at,
                    next_attempt_at=discovered_at,
                )
                session.add(record)
                session.flush()
                session.refresh(record)
                return self._to_task(record), True
        except IntegrityError:
            with self._session_factory.begin() as session:
                record = session.get(MediaTaskRecord, media.dedupe_key)
                if record is None:
                    raise
                self._refresh_media_fields(record, media, preview_path=preview_path)
                session.flush()
                return self._to_task(record), False

    async def start_task(self, task_id: str, started_at: datetime) -> MediaTask | None:
        with self._session_factory.begin() as session:
            record = session.get(MediaTaskRecord, task_id)
            if record is None or record.status not in {
                JobStatus.QUEUED.value,
                JobStatus.FAILED.value,
            }:
                return None
            return self._mark_running(record, started_at)

    async def start_next_task(self, started_at: datetime) -> MediaTask | None:
        with self._session_factory.begin() as session:
            record = session.execute(
                select(MediaTaskRecord)
                .where(MediaTaskRecord.status == JobStatus.QUEUED.value)
                .where(
                    (MediaTaskRecord.next_attempt_at.is_(None))
                    | (MediaTaskRecord.next_attempt_at <= started_at),
                )
                .order_by(
                    MediaTaskRecord.next_attempt_at.asc().nullsfirst(),
                    MediaTaskRecord.discovered_at.asc(),
                    MediaTaskRecord.id.asc(),
                )
                .limit(1),
                execution_options={"populate_existing": True},
            ).scalar_one_or_none()
            if record is None:
                return None
            return self._mark_running(record, started_at)

    async def start_feishu_sync(self, task_id: str, started_at: datetime) -> MediaTask | None:
        with self._session_factory.begin() as session:
            record = session.get(MediaTaskRecord, task_id)
            if record is None or record.status != JobStatus.SUCCEEDED.value:
                return None
            if record.feishu_sync_status not in {
                SyncStatus.PENDING.value,
                SyncStatus.FAILED.value,
            }:
                return None
            return self._mark_feishu_sync_running(record, started_at)

    async def start_next_feishu_sync(self, started_at: datetime) -> MediaTask | None:
        with self._session_factory.begin() as session:
            record = session.execute(
                select(MediaTaskRecord)
                .where(MediaTaskRecord.status == JobStatus.SUCCEEDED.value)
                .where(MediaTaskRecord.feishu_sync_status == SyncStatus.PENDING.value)
                .where(
                    (MediaTaskRecord.feishu_sync_next_attempt_at.is_(None))
                    | (MediaTaskRecord.feishu_sync_next_attempt_at <= started_at),
                )
                .order_by(
                    MediaTaskRecord.feishu_sync_next_attempt_at.asc().nullsfirst(),
                    MediaTaskRecord.updated_at.asc(),
                    MediaTaskRecord.id.asc(),
                )
                .limit(1),
                execution_options={"populate_existing": True},
            ).scalar_one_or_none()
            if record is None:
                return None
            return self._mark_feishu_sync_running(record, started_at)

    async def mark_succeeded(
        self,
        task_id: str,
        completed_at: datetime,
        outcome: PipelineOutcome,
    ) -> MediaTask:
        with self._session_factory.begin() as session:
            record = session.get(MediaTaskRecord, task_id)
            if record is None:
                raise KeyError(f"Unknown task {task_id}.")
            record.status = JobStatus.SUCCEEDED.value
            record.updated_at = completed_at
            record.finished_at = completed_at
            record.last_error = None
            record.screenshot_path = str(outcome.screenshot.path)
            record.feishu_sync_status = SyncStatus.PENDING.value
            record.feishu_sync_next_attempt_at = completed_at
            record.feishu_sync_last_error = None
            record.feishu_sync_last_error_kind = None
            record.event_time = outcome.analysis.event_time
            record.elimination_type = outcome.analysis.elimination_type.value
            record.stool_score = outcome.analysis.stool_score
            record.stool_shape_note = outcome.analysis.stool_shape_note
            record.confidence = outcome.analysis.confidence
            record.raw_summary = outcome.analysis.raw_summary
            session.flush()
            session.refresh(record)
            return self._to_task(record)

    async def mark_failed(
        self,
        task_id: str,
        failed_at: datetime,
        error: str,
        *,
        error_kind: str | None = None,
        next_attempt_at: datetime | None = None,
    ) -> MediaTask:
        with self._session_factory.begin() as session:
            record = session.get(MediaTaskRecord, task_id)
            if record is None:
                raise KeyError(f"Unknown task {task_id}.")
            record.status = JobStatus.QUEUED.value if next_attempt_at is not None else JobStatus.FAILED.value
            record.updated_at = failed_at
            record.finished_at = failed_at
            record.last_error = error
            record.last_error_kind = error_kind
            record.next_attempt_at = next_attempt_at
            session.flush()
            session.refresh(record)
            return self._to_task(record)

    async def mark_feishu_sync_succeeded(
        self,
        task_id: str,
        synced_at: datetime,
        *,
        feishu_record_id: str | None,
    ) -> MediaTask:
        with self._session_factory.begin() as session:
            record = session.get(MediaTaskRecord, task_id)
            if record is None:
                raise KeyError(f"Unknown task {task_id}.")
            record.updated_at = synced_at
            record.feishu_record_id = feishu_record_id or record.feishu_record_id
            record.feishu_sync_status = SyncStatus.SUCCEEDED.value
            record.feishu_sync_last_error = None
            record.feishu_sync_last_error_kind = None
            record.feishu_sync_next_attempt_at = None
            record.feishu_synced_at = synced_at
            session.flush()
            session.refresh(record)
            return self._to_task(record)

    async def mark_feishu_sync_failed(
        self,
        task_id: str,
        failed_at: datetime,
        error: str,
        *,
        error_kind: str | None = None,
        next_attempt_at: datetime | None = None,
    ) -> MediaTask:
        with self._session_factory.begin() as session:
            record = session.get(MediaTaskRecord, task_id)
            if record is None:
                raise KeyError(f"Unknown task {task_id}.")
            record.updated_at = failed_at
            record.feishu_sync_status = (
                SyncStatus.PENDING.value if next_attempt_at is not None else SyncStatus.FAILED.value
            )
            record.feishu_sync_last_error = error
            record.feishu_sync_last_error_kind = error_kind
            record.feishu_sync_next_attempt_at = next_attempt_at
            session.flush()
            session.refresh(record)
            return self._to_task(record)

    async def get_task(self, task_id: str) -> MediaTask | None:
        with self._session_factory() as session:
            record = session.get(MediaTaskRecord, task_id)
            if record is None:
                return None
            return self._to_task(record)

    async def list_tasks(self, *, limit: int | None = None) -> list[MediaTask]:
        with self._session_factory() as session:
            statement = select(MediaTaskRecord).order_by(
                MediaTaskRecord.updated_at.desc(),
                MediaTaskRecord.id.desc(),
            )
            if limit is not None:
                statement = statement.limit(limit)
            records = session.execute(statement).scalars().all()
            return [self._to_task(record) for record in records]

    async def recover_stale_tasks(
        self,
        *,
        stale_before: datetime,
        recovered_at: datetime,
    ) -> StaleRecoveryResult:
        with self._session_factory.begin() as session:
            analysis_records = session.execute(
                select(MediaTaskRecord)
                .where(MediaTaskRecord.status == JobStatus.RUNNING.value)
                .where(MediaTaskRecord.last_attempt_started_at.is_not(None))
                .where(MediaTaskRecord.last_attempt_started_at <= stale_before),
            ).scalars().all()
            for record in analysis_records:
                record.status = JobStatus.QUEUED.value
                record.updated_at = recovered_at
                record.finished_at = recovered_at
                record.last_error = (
                    "Recovered stale running analysis task after worker interruption."
                )
                record.last_error_kind = "stale_recovery"
                record.next_attempt_at = recovered_at

            feishu_records = session.execute(
                select(MediaTaskRecord)
                .where(MediaTaskRecord.feishu_sync_status == SyncStatus.RUNNING.value)
                .where(MediaTaskRecord.updated_at <= stale_before),
            ).scalars().all()
            for record in feishu_records:
                record.updated_at = recovered_at
                record.feishu_sync_status = SyncStatus.PENDING.value
                record.feishu_sync_last_error = (
                    "Recovered stale running Feishu sync after worker interruption."
                )
                record.feishu_sync_last_error_kind = "stale_recovery"
                record.feishu_sync_next_attempt_at = recovered_at

            return StaleRecoveryResult(
                analysis_recovered=len(analysis_records),
                feishu_sync_recovered=len(feishu_records),
            )

    def dispose(self) -> None:
        self._engine.dispose()

    def _ensure_compatible_schema(self) -> None:
        with self._engine.begin() as connection:
            schema = inspect(connection)
            if not schema.has_table("media_tasks"):
                return
            existing_columns = {column["name"] for column in schema.get_columns("media_tasks")}
            if "pet_name" not in existing_columns:
                connection.execute(text("ALTER TABLE media_tasks ADD COLUMN pet_name VARCHAR(255)"))
            if "preview_path" not in existing_columns:
                connection.execute(text("ALTER TABLE media_tasks ADD COLUMN preview_path TEXT"))
            if "last_error_kind" not in existing_columns:
                connection.execute(
                    text("ALTER TABLE media_tasks ADD COLUMN last_error_kind VARCHAR(64)"),
                )
            if "next_attempt_at" not in existing_columns:
                connection.execute(
                    text(
                        "ALTER TABLE media_tasks ADD COLUMN next_attempt_at "
                        f"{self._datetime_column_type(connection.engine.dialect.name)}"
                    ),
                )
            if "last_attempt_started_at" not in existing_columns:
                connection.execute(
                    text(
                        "ALTER TABLE media_tasks ADD COLUMN last_attempt_started_at "
                        f"{self._datetime_column_type(connection.engine.dialect.name)}"
                    ),
                )
            if "screenshot_path" not in existing_columns:
                connection.execute(text("ALTER TABLE media_tasks ADD COLUMN screenshot_path TEXT"))
            if "feishu_sync_status" not in existing_columns:
                connection.execute(text("ALTER TABLE media_tasks ADD COLUMN feishu_sync_status VARCHAR(32)"))
            if "feishu_sync_attempts" not in existing_columns:
                connection.execute(
                    text("ALTER TABLE media_tasks ADD COLUMN feishu_sync_attempts INTEGER DEFAULT 0"),
                )
            if "feishu_sync_last_error" not in existing_columns:
                connection.execute(text("ALTER TABLE media_tasks ADD COLUMN feishu_sync_last_error TEXT"))
            if "feishu_sync_last_error_kind" not in existing_columns:
                connection.execute(
                    text("ALTER TABLE media_tasks ADD COLUMN feishu_sync_last_error_kind VARCHAR(64)"),
                )
            if "feishu_sync_next_attempt_at" not in existing_columns:
                connection.execute(
                    text(
                        "ALTER TABLE media_tasks ADD COLUMN feishu_sync_next_attempt_at "
                        f"{self._datetime_column_type(connection.engine.dialect.name)}"
                    ),
                )
            if "feishu_synced_at" not in existing_columns:
                connection.execute(
                    text(
                        "ALTER TABLE media_tasks ADD COLUMN feishu_synced_at "
                        f"{self._datetime_column_type(connection.engine.dialect.name)}"
                    ),
                )
            existing_indexes = {index["name"] for index in schema.get_indexes("media_tasks")}
            if "ix_media_tasks_next_attempt_at" not in existing_indexes:
                connection.execute(
                    text("CREATE INDEX ix_media_tasks_next_attempt_at ON media_tasks (next_attempt_at)"),
                )
            if "ix_media_tasks_feishu_sync_status" not in existing_indexes:
                connection.execute(
                    text("CREATE INDEX ix_media_tasks_feishu_sync_status ON media_tasks (feishu_sync_status)"),
                )
            if "ix_media_tasks_feishu_sync_next_attempt_at" not in existing_indexes:
                connection.execute(
                    text(
                        "CREATE INDEX ix_media_tasks_feishu_sync_next_attempt_at "
                        "ON media_tasks (feishu_sync_next_attempt_at)"
                    ),
                )

    def _mark_running(self, record: MediaTaskRecord, started_at: datetime) -> MediaTask:
        record.status = JobStatus.RUNNING.value
        record.updated_at = started_at
        record.last_error = None
        record.last_error_kind = None
        record.finished_at = None
        record.next_attempt_at = None
        record.last_attempt_started_at = started_at
        record.attempts += 1
        return self._to_task(record)

    def _mark_feishu_sync_running(self, record: MediaTaskRecord, started_at: datetime) -> MediaTask:
        record.updated_at = started_at
        record.feishu_sync_status = SyncStatus.RUNNING.value
        record.feishu_sync_attempts += 1
        record.feishu_sync_last_error = None
        record.feishu_sync_last_error_kind = None
        record.feishu_sync_next_attempt_at = None
        return self._to_task(record)

    @staticmethod
    def _refresh_media_fields(
        record: MediaTaskRecord,
        media: PetKitMedia,
        *,
        preview_path: Path | None = None,
    ) -> None:
        record.media_id = media.id
        record.device_id = media.device_id
        record.media_started_at = media.started_at
        record.cover_url = media.cover_url
        record.encrypted_download_url = media.encrypted_download_url
        record.source_day = media.source_day
        record.pet_name = media.pet_name
        if preview_path is not None:
            record.preview_path = str(preview_path)

    @staticmethod
    def _to_task(record: MediaTaskRecord) -> MediaTask:
        elimination_type = None
        if record.elimination_type:
            try:
                elimination_type = EliminationType(record.elimination_type)
            except ValueError:
                elimination_type = EliminationType.UNKNOWN
        feishu_sync_status = None
        if record.feishu_sync_status:
            try:
                feishu_sync_status = SyncStatus(record.feishu_sync_status)
            except ValueError:
                feishu_sync_status = SyncStatus.FAILED
        return MediaTask(
            id=record.id,
            media=PetKitMedia(
                id=record.media_id,
                device_id=record.device_id,
                started_at=SqlAlchemyMediaTaskStore._coerce_datetime(record.media_started_at),
                cover_url=record.cover_url,
                encrypted_download_url=record.encrypted_download_url,
                source_day=record.source_day,
                pet_name=record.pet_name,
            ),
            status=JobStatus(record.status),
            discovered_at=SqlAlchemyMediaTaskStore._coerce_datetime(record.discovered_at),
            updated_at=SqlAlchemyMediaTaskStore._coerce_datetime(record.updated_at),
            attempts=record.attempts,
            last_error=record.last_error,
            last_error_kind=record.last_error_kind,
            next_attempt_at=SqlAlchemyMediaTaskStore._coerce_datetime(record.next_attempt_at),
            finished_at=SqlAlchemyMediaTaskStore._coerce_datetime(record.finished_at),
            preview_path=None if record.preview_path is None else Path(record.preview_path),
            screenshot_path=None if record.screenshot_path is None else Path(record.screenshot_path),
            feishu_record_id=record.feishu_record_id,
            feishu_sync_status=feishu_sync_status,
            feishu_sync_attempts=record.feishu_sync_attempts,
            feishu_sync_last_error=record.feishu_sync_last_error,
            feishu_sync_last_error_kind=record.feishu_sync_last_error_kind,
            feishu_sync_next_attempt_at=SqlAlchemyMediaTaskStore._coerce_datetime(
                record.feishu_sync_next_attempt_at,
            ),
            feishu_synced_at=SqlAlchemyMediaTaskStore._coerce_datetime(record.feishu_synced_at),
            event_time=SqlAlchemyMediaTaskStore._coerce_datetime(record.event_time),
            elimination_type=elimination_type,
            stool_score=record.stool_score,
            stool_shape_note=record.stool_shape_note,
            confidence=record.confidence,
            raw_summary=record.raw_summary,
        )

    @staticmethod
    def _coerce_datetime(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    @staticmethod
    def _datetime_column_type(dialect_name: str) -> str:
        if dialect_name == "postgresql":
            return "TIMESTAMP WITH TIME ZONE"
        return "DATETIME"
