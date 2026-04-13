from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from meow_toilet.domain.entities import (
    AnalysisResult,
    EliminationType,
    JobStatus,
    PetKitMedia,
    PipelineOutcome,
    ScreenshotArtifact,
    SyncStatus,
)
from meow_toilet.services.sql_task_store import SqlAlchemyMediaTaskStore


def test_sql_task_store_persists_and_updates_task_lifecycle(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'tasks.db'}"
    store = SqlAlchemyMediaTaskStore(database_url)
    media = PetKitMedia(
        id="media-1",
        device_id="device-1",
        started_at=datetime(2026, 4, 9, 9, 0, tzinfo=UTC),
        cover_url="https://example.com/cover.jpg",
        encrypted_download_url="https://example.com/video.mp4",
        source_day="2026-04-09",
        pet_name="翠饼",
    )

    async def run_test() -> None:
        created_task, created = await store.enqueue_media(
            media,
            discovered_at=datetime(2026, 4, 9, 9, 1, tzinfo=UTC),
        )
        duplicate_task, duplicate_created = await store.enqueue_media(
            media,
            discovered_at=datetime(2026, 4, 9, 9, 2, tzinfo=UTC),
        )
        running_task = await store.start_task(
            created_task.id,
            started_at=datetime(2026, 4, 9, 9, 3, tzinfo=UTC),
        )
        assert running_task is not None

        completed_task = await store.mark_succeeded(
            created_task.id,
            completed_at=datetime(2026, 4, 9, 9, 4, tzinfo=UTC),
            outcome=PipelineOutcome(
                media_id=media.id,
                screenshot=ScreenshotArtifact(
                    path=tmp_path / "event.jpg",
                    captured_at=datetime(2026, 4, 9, 9, 0, 3, tzinfo=UTC),
                ),
                analysis=AnalysisResult(
                    event_time=datetime(2026, 4, 9, 9, 0, 3, tzinfo=UTC),
                    event_offset_seconds=3.0,
                    elimination_type=EliminationType.POOP,
                    stool_score="4",
                    stool_shape_note="compact",
                    confidence=0.92,
                    raw_summary="Poop event detected.",
                ),
                feishu_record_id="rec-1",
            ),
        )
        listed = await store.list_tasks()
        fetched = await store.get_task(created_task.id)

        assert created is True
        assert duplicate_created is False
        assert duplicate_task.id == created_task.id
        assert created_task.status == JobStatus.QUEUED
        assert running_task.status == JobStatus.RUNNING
        assert running_task.attempts == 1
        assert completed_task.status == JobStatus.SUCCEEDED
        assert completed_task.feishu_record_id is None
        assert completed_task.feishu_sync_status == SyncStatus.PENDING
        assert completed_task.stool_score == "4"
        assert completed_task.raw_summary == "Poop event detected."
        assert fetched is not None
        assert fetched.status == JobStatus.SUCCEEDED
        assert fetched.preview_path is None
        assert fetched.screenshot_path is not None
        assert fetched.media.pet_name == "翠饼"
        assert fetched.event_time is not None
        assert fetched.event_time.tzinfo is not None
        assert len(listed) == 1

    try:
        asyncio.run(run_test())
    finally:
        store.dispose()


def test_sql_task_store_persists_preview_path_from_enqueue(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'tasks-preview.db'}"
    store = SqlAlchemyMediaTaskStore(database_url)
    media = PetKitMedia(
        id="media-preview-1",
        device_id="device-preview-1",
        started_at=datetime(2026, 4, 9, 9, 0, tzinfo=UTC),
        cover_url="https://example.com/cover.jpg",
        encrypted_download_url="https://example.com/video.mp4",
        source_day="2026-04-09",
        pet_name="翠饼",
    )
    preview_path = tmp_path / "preview.jpg"
    preview_path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

    async def run_test() -> None:
        created_task, created = await store.enqueue_media(
            media,
            discovered_at=datetime(2026, 4, 9, 9, 1, tzinfo=UTC),
            preview_path=preview_path,
        )
        fetched = await store.get_task(created_task.id)

        assert created is True
        assert fetched is not None
        assert fetched.preview_path == preview_path
        assert fetched.media.cover_url == "https://example.com/cover.jpg"

    try:
        asyncio.run(run_test())
    finally:
        store.dispose()


def test_sql_task_store_tracks_feishu_sync_lifecycle(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'tasks-sync.db'}"
    store = SqlAlchemyMediaTaskStore(database_url)
    media = PetKitMedia(
        id="media-5",
        device_id="device-5",
        started_at=datetime(2026, 4, 9, 13, 0, tzinfo=UTC),
        cover_url=None,
        encrypted_download_url="https://example.com/video-5.mp4",
        source_day="2026-04-09",
    )
    screenshot_path = tmp_path / "event-5.jpg"
    screenshot_path.write_bytes(b"fake")

    async def run_test() -> None:
        task, _ = await store.enqueue_media(
            media,
            discovered_at=datetime(2026, 4, 9, 13, 1, tzinfo=UTC),
        )
        await store.start_task(
            task.id,
            started_at=datetime(2026, 4, 9, 13, 2, tzinfo=UTC),
        )
        analyzed = await store.mark_succeeded(
            task.id,
            completed_at=datetime(2026, 4, 9, 13, 3, tzinfo=UTC),
            outcome=PipelineOutcome(
                media_id=media.id,
                screenshot=ScreenshotArtifact(
                    path=screenshot_path,
                    captured_at=datetime(2026, 4, 9, 13, 0, 3, tzinfo=UTC),
                ),
                analysis=AnalysisResult(
                    event_time=datetime(2026, 4, 9, 13, 0, 3, tzinfo=UTC),
                    event_offset_seconds=3.0,
                    elimination_type=EliminationType.PEE,
                    stool_score=None,
                    stool_shape_note=None,
                    confidence=0.72,
                    raw_summary="Pee event detected.",
                ),
                feishu_record_id=None,
            ),
        )
        syncing = await store.start_next_feishu_sync(
            started_at=datetime(2026, 4, 9, 13, 4, tzinfo=UTC),
        )
        synced = await store.mark_feishu_sync_succeeded(
            task.id,
            synced_at=datetime(2026, 4, 9, 13, 5, tzinfo=UTC),
            feishu_record_id="rec-sync-1",
        )

        assert analyzed.feishu_sync_status == SyncStatus.PENDING
        assert syncing is not None
        assert syncing.feishu_sync_status == SyncStatus.RUNNING
        assert synced.feishu_sync_status == SyncStatus.SUCCEEDED
        assert synced.feishu_record_id == "rec-sync-1"
        assert synced.feishu_synced_at == datetime(2026, 4, 9, 13, 5, tzinfo=UTC)

    try:
        asyncio.run(run_test())
    finally:
        store.dispose()


def test_sql_task_store_marks_failures(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'tasks-failed.db'}"
    store = SqlAlchemyMediaTaskStore(database_url)
    media = PetKitMedia(
        id="media-2",
        device_id="device-2",
        started_at=datetime(2026, 4, 9, 10, 0, tzinfo=UTC),
        cover_url=None,
        encrypted_download_url="https://example.com/video-2.mp4",
        source_day="2026-04-09",
    )

    async def run_test() -> None:
        task, _created = await store.enqueue_media(
            media,
            discovered_at=datetime(2026, 4, 9, 10, 1, tzinfo=UTC),
        )
        started = await store.start_next_task(
            started_at=datetime(2026, 4, 9, 10, 2, tzinfo=UTC),
        )
        failed = await store.mark_failed(
            task.id,
            failed_at=datetime(2026, 4, 9, 10, 3, tzinfo=UTC),
            error="decode failed",
        )

        assert started is not None
        assert failed.status == JobStatus.FAILED
        assert failed.last_error == "decode failed"
        assert failed.finished_at is not None

    try:
        asyncio.run(run_test())
    finally:
        store.dispose()


def test_sql_task_store_allows_retrying_failed_task(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'tasks-retry.db'}"
    store = SqlAlchemyMediaTaskStore(database_url)
    media = PetKitMedia(
        id="media-3",
        device_id="device-3",
        started_at=datetime(2026, 4, 9, 11, 0, tzinfo=UTC),
        cover_url=None,
        encrypted_download_url="https://example.com/video-3.mp4",
        source_day="2026-04-09",
    )

    async def run_test() -> None:
        task, _created = await store.enqueue_media(
            media,
            discovered_at=datetime(2026, 4, 9, 11, 1, tzinfo=UTC),
        )
        await store.start_task(
            task.id,
            started_at=datetime(2026, 4, 9, 11, 2, tzinfo=UTC),
        )
        await store.mark_failed(
            task.id,
            failed_at=datetime(2026, 4, 9, 11, 3, tzinfo=UTC),
            error="first failure",
        )

        retried = await store.start_task(
            task.id,
            started_at=datetime(2026, 4, 9, 11, 4, tzinfo=UTC),
        )

        assert retried is not None
        assert retried.status == JobStatus.RUNNING
        assert retried.attempts == 2
        assert retried.last_error is None
        assert retried.finished_at is None

    try:
        asyncio.run(run_test())
    finally:
        store.dispose()


def test_sql_task_store_keeps_retryable_failure_queued_until_backoff_expires(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'tasks-backoff.db'}"
    store = SqlAlchemyMediaTaskStore(database_url)
    media = PetKitMedia(
        id="media-4",
        device_id="device-4",
        started_at=datetime(2026, 4, 9, 12, 0, tzinfo=UTC),
        cover_url=None,
        encrypted_download_url="https://example.com/video-4.mp4",
        source_day="2026-04-09",
    )

    async def run_test() -> None:
        task, _created = await store.enqueue_media(
            media,
            discovered_at=datetime(2026, 4, 9, 12, 1, tzinfo=UTC),
        )
        await store.start_task(
            task.id,
            started_at=datetime(2026, 4, 9, 12, 2, tzinfo=UTC),
        )
        retry_task = await store.mark_failed(
            task.id,
            failed_at=datetime(2026, 4, 9, 12, 3, tzinfo=UTC),
            error="temporary timeout",
            error_kind="timeout",
            next_attempt_at=datetime(2026, 4, 9, 12, 8, tzinfo=UTC),
        )
        not_ready = await store.start_next_task(
            started_at=datetime(2026, 4, 9, 12, 7, tzinfo=UTC),
        )
        ready = await store.start_next_task(
            started_at=datetime(2026, 4, 9, 12, 8, tzinfo=UTC),
        )

        assert retry_task.status == JobStatus.QUEUED
        assert retry_task.last_error_kind == "timeout"
        assert retry_task.next_attempt_at == datetime(2026, 4, 9, 12, 8, tzinfo=UTC)
        assert not_ready is None
        assert ready is not None
        assert ready.status == JobStatus.RUNNING
        assert ready.attempts == 2

    try:
        asyncio.run(run_test())
    finally:
        store.dispose()


def test_sql_task_store_recovers_stale_running_states(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'tasks-stale.db'}"
    store = SqlAlchemyMediaTaskStore(database_url)
    analysis_media = PetKitMedia(
        id="media-stale-analysis",
        device_id="device-stale-analysis",
        started_at=datetime(2026, 4, 9, 18, 0, tzinfo=UTC),
        cover_url=None,
        encrypted_download_url="https://example.com/video-stale-analysis.mp4",
        source_day="2026-04-09",
    )
    sync_media = PetKitMedia(
        id="media-stale-sync",
        device_id="device-stale-sync",
        started_at=datetime(2026, 4, 9, 18, 30, tzinfo=UTC),
        cover_url=None,
        encrypted_download_url="https://example.com/video-stale-sync.mp4",
        source_day="2026-04-09",
    )
    screenshot_path = tmp_path / "stale-sync.jpg"
    screenshot_path.write_bytes(b"fake")

    async def run_test() -> None:
        analysis_task, _ = await store.enqueue_media(
            analysis_media,
            discovered_at=datetime(2026, 4, 9, 18, 0, tzinfo=UTC),
        )
        sync_task, _ = await store.enqueue_media(
            sync_media,
            discovered_at=datetime(2026, 4, 9, 18, 30, tzinfo=UTC),
        )
        await store.start_task(
            analysis_task.id,
            started_at=datetime(2026, 4, 9, 18, 0, 1, tzinfo=UTC),
        )
        await store.start_task(
            sync_task.id,
            started_at=datetime(2026, 4, 9, 18, 30, 1, tzinfo=UTC),
        )
        await store.mark_succeeded(
            sync_task.id,
            completed_at=datetime(2026, 4, 9, 18, 30, 2, tzinfo=UTC),
            outcome=PipelineOutcome(
                media_id=sync_media.id,
                screenshot=ScreenshotArtifact(
                    path=screenshot_path,
                    captured_at=datetime(2026, 4, 9, 18, 30, 2, tzinfo=UTC),
                ),
                analysis=AnalysisResult(
                    event_time=datetime(2026, 4, 9, 18, 30, 2, tzinfo=UTC),
                    event_offset_seconds=2.0,
                    elimination_type=EliminationType.PEE,
                    stool_score=None,
                    stool_shape_note=None,
                    confidence=0.8,
                    raw_summary="Pee event detected.",
                ),
                feishu_record_id=None,
            ),
        )
        await store.start_feishu_sync(
            sync_task.id,
            started_at=datetime(2026, 4, 9, 18, 30, 3, tzinfo=UTC),
        )

        recovery = await store.recover_stale_tasks(
            stale_before=datetime(2026, 4, 9, 19, 0, tzinfo=UTC),
            recovered_at=datetime(2026, 4, 9, 19, 0, 1, tzinfo=UTC),
        )
        recovered_analysis = await store.get_task(analysis_task.id)
        recovered_sync = await store.get_task(sync_task.id)

        assert recovery.analysis_recovered == 1
        assert recovery.feishu_sync_recovered == 1
        assert recovered_analysis is not None
        assert recovered_analysis.status == JobStatus.QUEUED
        assert recovered_analysis.last_error_kind == "stale_recovery"
        assert recovered_sync is not None
        assert recovered_sync.feishu_sync_status == SyncStatus.PENDING
        assert recovered_sync.feishu_sync_last_error_kind == "stale_recovery"

    try:
        asyncio.run(run_test())
    finally:
        store.dispose()
