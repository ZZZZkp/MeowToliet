from __future__ import annotations

import asyncio
from datetime import datetime, timezone
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
from meow_toilet.errors import ExternalServiceError
from meow_toilet.services.retries import RetryPolicy
from meow_toilet.services.task_store import InMemoryMediaTaskStore
from meow_toilet.workers.jobs import MediaJobWorker


class SuccessfulPipeline:
    async def run(self, request) -> PipelineOutcome:
        analysis = AnalysisResult(
            event_time=request.media.started_at,
            event_offset_seconds=5.0,
            elimination_type=EliminationType.POOP,
            stool_score="4",
            stool_shape_note="formed",
            confidence=0.91,
            raw_summary="Detected a poop event.",
        )
        return PipelineOutcome(
            media_id=request.media.id,
            screenshot=ScreenshotArtifact(
                path=Path("/tmp/ignored.jpg"),
                captured_at=request.media.started_at,
            ),
            analysis=analysis,
            feishu_record_id=None,
        )


class FailingPipeline:
    async def run(self, request) -> PipelineOutcome:
        raise RuntimeError(f"boom for {request.media.id}")


class RetryableFailingPipeline:
    async def run(self, request) -> PipelineOutcome:
        raise ExternalServiceError(
            service="gemini",
            operation="generate_content",
            message="Timed out waiting for Gemini.",
            kind="timeout",
            retryable=True,
        )


class SuccessfulFeishuSyncService:
    async def sync_task(self, task: MediaTask) -> str | None:
        return "rec-123"


class RetryableFailingFeishuSyncService:
    async def sync_task(self, task: MediaTask) -> str | None:
        raise ExternalServiceError(
            service="feishu",
            operation="create_record",
            message="rate limited",
            kind="rate_limit",
            retryable=True,
        )


def test_worker_marks_task_succeeded_after_pipeline_run() -> None:
    media = PetKitMedia(
        id="media-1",
        device_id="device-1",
        started_at=datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc),
        cover_url="https://example.com/cover.jpg",
        encrypted_download_url="https://example.com/video.mp4",
        source_day="2026-04-09",
    )
    task_store = InMemoryMediaTaskStore()
    clock = iter(
        [
            datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 10, 1, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 10, 2, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 10, 3, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 10, 4, tzinfo=timezone.utc),
        ],
    )
    worker = MediaJobWorker(
        task_store=task_store,
        pipeline=SuccessfulPipeline(),
        feishu_sync_service=SuccessfulFeishuSyncService(),
        retry_policy=RetryPolicy(max_attempts=5, backoff_seconds=30, max_backoff_seconds=900),
        now_provider=lambda: next(clock),
    )

    async def run_test() -> None:
        await task_store.enqueue_media(media, discovered_at=datetime(2026, 4, 9, 9, 59, tzinfo=timezone.utc))
        task = await worker.process_next_job()

        assert task is not None
        assert task.status == JobStatus.SUCCEEDED
        assert task.attempts == 1
        assert task.feishu_record_id is None
        assert task.feishu_sync_status == SyncStatus.PENDING
        assert task.elimination_type == EliminationType.POOP
        assert task.confidence == 0.91
        synced = await worker.process_next_job()
        assert synced is not None
        assert synced.feishu_record_id == "rec-123"
        assert synced.feishu_sync_status == SyncStatus.SUCCEEDED

    asyncio.run(run_test())


def test_worker_marks_task_failed_when_pipeline_raises() -> None:
    media = PetKitMedia(
        id="media-2",
        device_id="device-2",
        started_at=datetime(2026, 4, 9, 11, 0, tzinfo=timezone.utc),
        cover_url=None,
        encrypted_download_url="https://example.com/video-2.mp4",
        source_day="2026-04-09",
    )
    task_store = InMemoryMediaTaskStore()
    clock = iter(
        [
            datetime(2026, 4, 9, 11, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 11, 1, tzinfo=timezone.utc),
        ],
    )
    worker = MediaJobWorker(
        task_store=task_store,
        pipeline=FailingPipeline(),
        feishu_sync_service=SuccessfulFeishuSyncService(),
        retry_policy=RetryPolicy(max_attempts=5, backoff_seconds=30, max_backoff_seconds=900),
        now_provider=lambda: next(clock),
    )

    async def run_test() -> None:
        await task_store.enqueue_media(media, discovered_at=datetime(2026, 4, 9, 10, 59, tzinfo=timezone.utc))
        task = await worker.process_next_job()

        assert task is not None
        assert task.status == JobStatus.FAILED
        assert task.attempts == 1
        assert task.last_error == "boom for media-2"

    asyncio.run(run_test())


def test_worker_schedules_retry_with_backoff_for_retryable_failures() -> None:
    media = PetKitMedia(
        id="media-3",
        device_id="device-3",
        started_at=datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc),
        cover_url=None,
        encrypted_download_url="https://example.com/video-3.mp4",
        source_day="2026-04-09",
    )
    task_store = InMemoryMediaTaskStore()
    clock = iter(
        [
            datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 12, 1, tzinfo=timezone.utc),
        ],
    )
    worker = MediaJobWorker(
        task_store=task_store,
        pipeline=RetryableFailingPipeline(),
        feishu_sync_service=SuccessfulFeishuSyncService(),
        retry_policy=RetryPolicy(max_attempts=5, backoff_seconds=30, max_backoff_seconds=900),
        now_provider=lambda: next(clock),
    )

    async def run_test() -> None:
        await task_store.enqueue_media(media, discovered_at=datetime(2026, 4, 9, 11, 59, tzinfo=timezone.utc))
        task = await worker.process_next_job()

        assert task is not None
        assert task.status == JobStatus.QUEUED
        assert task.attempts == 1
        assert task.last_error_kind == "timeout"
        assert task.last_error is not None
        assert task.next_attempt_at == datetime(2026, 4, 9, 12, 1, 30, tzinfo=timezone.utc)

    asyncio.run(run_test())


def test_worker_retries_feishu_sync_after_analysis_succeeds() -> None:
    media = PetKitMedia(
        id="media-4",
        device_id="device-4",
        started_at=datetime(2026, 4, 9, 13, 0, tzinfo=timezone.utc),
        cover_url=None,
        encrypted_download_url="https://example.com/video-4.mp4",
        source_day="2026-04-09",
    )
    task_store = InMemoryMediaTaskStore()
    clock = iter(
        [
            datetime(2026, 4, 9, 13, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 13, 1, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 13, 2, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 13, 3, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 13, 4, tzinfo=timezone.utc),
        ],
    )
    worker = MediaJobWorker(
        task_store=task_store,
        pipeline=SuccessfulPipeline(),
        feishu_sync_service=RetryableFailingFeishuSyncService(),
        retry_policy=RetryPolicy(max_attempts=5, backoff_seconds=30, max_backoff_seconds=900),
        now_provider=lambda: next(clock),
    )

    async def run_test() -> None:
        await task_store.enqueue_media(
            media,
            discovered_at=datetime(2026, 4, 9, 12, 59, tzinfo=timezone.utc),
        )
        analyzed = await worker.process_next_job()
        retried_sync = await worker.process_next_job()

        assert analyzed is not None
        assert analyzed.status == JobStatus.SUCCEEDED
        assert analyzed.feishu_sync_status == SyncStatus.PENDING
        assert retried_sync is not None
        assert retried_sync.feishu_sync_status == SyncStatus.PENDING
        assert retried_sync.feishu_sync_attempts == 1
        assert retried_sync.feishu_sync_last_error_kind == "rate_limit"
        assert retried_sync.feishu_sync_next_attempt_at == datetime(
            2026,
            4,
            9,
            13,
            4,
            30,
            tzinfo=timezone.utc,
        )

    asyncio.run(run_test())
