from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker

from meow_toilet.domain.entities import (
    EliminationType,
    JobStatus,
    MediaTask,
    PetKitMedia,
    PipelineOutcome,
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
    status = Column(String(32), nullable=False, index=True)
    discovered_at = Column(DateTime(timezone=True), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, index=True)
    attempts = Column(Integer(), nullable=False, default=0)
    last_error = Column(Text(), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    feishu_record_id = Column(String(255), nullable=True)
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

    async def enqueue_media(
        self,
        media: PetKitMedia,
        discovered_at: datetime,
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
                    status=JobStatus.QUEUED.value,
                    discovered_at=discovered_at,
                    updated_at=discovered_at,
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
                self._refresh_media_fields(record, media)
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
                .order_by(MediaTaskRecord.discovered_at.asc(), MediaTaskRecord.id.asc())
                .limit(1),
            ).scalar_one_or_none()
            if record is None:
                return None
            return self._mark_running(record, started_at)

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
            record.feishu_record_id = outcome.feishu_record_id
            record.event_time = outcome.analysis.event_time
            record.elimination_type = outcome.analysis.elimination_type.value
            record.stool_score = outcome.analysis.stool_score
            record.stool_shape_note = outcome.analysis.stool_shape_note
            record.confidence = outcome.analysis.confidence
            record.raw_summary = outcome.analysis.raw_summary
            session.flush()
            session.refresh(record)
            return self._to_task(record)

    async def mark_failed(self, task_id: str, failed_at: datetime, error: str) -> MediaTask:
        with self._session_factory.begin() as session:
            record = session.get(MediaTaskRecord, task_id)
            if record is None:
                raise KeyError(f"Unknown task {task_id}.")
            record.status = JobStatus.FAILED.value
            record.updated_at = failed_at
            record.finished_at = failed_at
            record.last_error = error
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

    def dispose(self) -> None:
        self._engine.dispose()

    def _mark_running(self, record: MediaTaskRecord, started_at: datetime) -> MediaTask:
        record.status = JobStatus.RUNNING.value
        record.updated_at = started_at
        record.last_error = None
        record.finished_at = None
        record.attempts += 1
        return self._to_task(record)

    @staticmethod
    def _refresh_media_fields(record: MediaTaskRecord, media: PetKitMedia) -> None:
        record.media_id = media.id
        record.device_id = media.device_id
        record.media_started_at = media.started_at
        record.cover_url = media.cover_url
        record.encrypted_download_url = media.encrypted_download_url
        record.source_day = media.source_day

    @staticmethod
    def _to_task(record: MediaTaskRecord) -> MediaTask:
        elimination_type = None
        if record.elimination_type:
            try:
                elimination_type = EliminationType(record.elimination_type)
            except ValueError:
                elimination_type = EliminationType.UNKNOWN
        return MediaTask(
            id=record.id,
            media=PetKitMedia(
                id=record.media_id,
                device_id=record.device_id,
                started_at=SqlAlchemyMediaTaskStore._coerce_datetime(record.media_started_at),
                cover_url=record.cover_url,
                encrypted_download_url=record.encrypted_download_url,
                source_day=record.source_day,
            ),
            status=JobStatus(record.status),
            discovered_at=SqlAlchemyMediaTaskStore._coerce_datetime(record.discovered_at),
            updated_at=SqlAlchemyMediaTaskStore._coerce_datetime(record.updated_at),
            attempts=record.attempts,
            last_error=record.last_error,
            finished_at=SqlAlchemyMediaTaskStore._coerce_datetime(record.finished_at),
            feishu_record_id=record.feishu_record_id,
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
