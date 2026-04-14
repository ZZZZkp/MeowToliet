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
            datetime(2026, 4, 9, 10, 5, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 10, 6, tzinfo=timezone.utc),
        ],
    )
    worker = MediaJobWorker(
        task_store=task_store,
        pipeline=SuccessfulPipeline(),
        feishu_sync_service=SuccessfulFeishuSyncService(),
        retry_policy=RetryPolicy(max_attempts=5, backoff_seconds=30, max_backoff_seconds=900),
        stale_task_timeout_seconds=1800,
        now_provider=lambda: next(clock),
    )

    async def run_test() -> None:
        await task_store.enqueue_media(media, discovered_at=datetime(2026, 4, 9, 9, 59, tzinfo=timezone.utc))
        task = await worker.process_next_job()

        assert task is not None
        assert task.status == JobStatus.SUCCEEDED
        assert task.attempts == 1
        assert task.feishu_record_id == "rec-123"
        assert task.feishu_sync_status == SyncStatus.SUCCEEDED
        assert task.feishu_sync_attempts == 1
        assert task.elimination_type == EliminationType.POOP
        assert task.confidence == 0.91

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
            datetime(2026, 4, 9, 11, 2, tzinfo=timezone.utc),
        ],
    )
    worker = MediaJobWorker(
        task_store=task_store,
        pipeline=FailingPipeline(),
        feishu_sync_service=SuccessfulFeishuSyncService(),
        retry_policy=RetryPolicy(max_attempts=5, backoff_seconds=30, max_backoff_seconds=900),
        stale_task_timeout_seconds=1800,
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
            datetime(2026, 4, 9, 12, 1, 30, tzinfo=timezone.utc),
        ],
    )
    worker = MediaJobWorker(
        task_store=task_store,
        pipeline=RetryableFailingPipeline(),
        feishu_sync_service=SuccessfulFeishuSyncService(),
        retry_policy=RetryPolicy(max_attempts=5, backoff_seconds=30, max_backoff_seconds=900),
        stale_task_timeout_seconds=1800,
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
        assert task.next_attempt_at == datetime(2026, 4, 9, 12, 2, 0, tzinfo=timezone.utc)

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
            datetime(2026, 4, 9, 13, 4, 30, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 13, 5, 0, tzinfo=timezone.utc),
        ],
    )
    worker = MediaJobWorker(
        task_store=task_store,
        pipeline=SuccessfulPipeline(),
        feishu_sync_service=RetryableFailingFeishuSyncService(),
        retry_policy=RetryPolicy(max_attempts=5, backoff_seconds=30, max_backoff_seconds=900),
        stale_task_timeout_seconds=1800,
        now_provider=lambda: next(clock),
    )

    async def run_test() -> None:
        await task_store.enqueue_media(
            media,
            discovered_at=datetime(2026, 4, 9, 12, 59, tzinfo=timezone.utc),
        )
        retried_sync = await worker.process_next_job()

        assert retried_sync is not None
        assert retried_sync.status == JobStatus.SUCCEEDED
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


def test_worker_process_task_runs_analysis_and_feishu_sync_in_one_call() -> None:
    media = PetKitMedia(
        id="media-manual-1",
        device_id="device-manual-1",
        started_at=datetime(2026, 4, 9, 13, 30, tzinfo=timezone.utc),
        cover_url=None,
        encrypted_download_url="https://example.com/video-manual-1.mp4",
        source_day="2026-04-09",
    )
    task_store = InMemoryMediaTaskStore()
    clock = iter(
        [
            datetime(2026, 4, 9, 13, 30, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 13, 31, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 13, 32, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 13, 33, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 13, 34, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 13, 35, tzinfo=timezone.utc),
        ],
    )
    worker = MediaJobWorker(
        task_store=task_store,
        pipeline=SuccessfulPipeline(),
        feishu_sync_service=SuccessfulFeishuSyncService(),
        retry_policy=RetryPolicy(max_attempts=5, backoff_seconds=30, max_backoff_seconds=900),
        stale_task_timeout_seconds=1800,
        now_provider=lambda: next(clock),
    )

    async def run_test() -> None:
        task, _ = await task_store.enqueue_media(
            media,
            discovered_at=datetime(2026, 4, 9, 13, 29, tzinfo=timezone.utc),
        )
        processed = await worker.process_task(task.id)

        assert processed is not None
        assert processed.status == JobStatus.SUCCEEDED
        assert processed.feishu_sync_status == SyncStatus.SUCCEEDED
        assert processed.feishu_record_id == "rec-123"
        assert processed.feishu_sync_attempts == 1

    asyncio.run(run_test())


def test_worker_recovers_stale_running_analysis_task() -> None:
    media = PetKitMedia(
        id="media-stale-analysis",
        device_id="device-stale-analysis",
        started_at=datetime(2026, 4, 9, 14, 0, tzinfo=timezone.utc),
        cover_url=None,
        encrypted_download_url="https://example.com/video-stale-analysis.mp4",
        source_day="2026-04-09",
    )
    task_store = InMemoryMediaTaskStore()
    clock = iter(
        [
            datetime(2026, 4, 9, 15, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 15, 0, 1, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 15, 0, 2, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 15, 0, 3, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 15, 0, 4, tzinfo=timezone.utc),
        ],
    )
    worker = MediaJobWorker(
        task_store=task_store,
        pipeline=SuccessfulPipeline(),
        feishu_sync_service=SuccessfulFeishuSyncService(),
        retry_policy=RetryPolicy(max_attempts=5, backoff_seconds=30, max_backoff_seconds=900),
        stale_task_timeout_seconds=1800,
        now_provider=lambda: next(clock),
    )

    async def run_test() -> None:
        task, _ = await task_store.enqueue_media(
            media,
            discovered_at=datetime(2026, 4, 9, 14, 0, tzinfo=timezone.utc),
        )
        await task_store.start_task(
            task.id,
            started_at=datetime(2026, 4, 9, 14, 0, 1, tzinfo=timezone.utc),
        )
        processed = await worker.process_next_job()

        assert processed is not None
        assert processed.status == JobStatus.SUCCEEDED
        assert processed.attempts == 2

    asyncio.run(run_test())


def test_worker_recovers_stale_running_feishu_sync() -> None:
    media = PetKitMedia(
        id="media-stale-feishu",
        device_id="device-stale-feishu",
        started_at=datetime(2026, 4, 9, 16, 0, tzinfo=timezone.utc),
        cover_url=None,
        encrypted_download_url="https://example.com/video-stale-feishu.mp4",
        source_day="2026-04-09",
    )
    task_store = InMemoryMediaTaskStore()
    clock = iter(
        [
            datetime(2026, 4, 9, 17, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 17, 0, 1, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 17, 0, 2, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 17, 0, 3, tzinfo=timezone.utc),
        ],
    )
    worker = MediaJobWorker(
        task_store=task_store,
        pipeline=SuccessfulPipeline(),
        feishu_sync_service=SuccessfulFeishuSyncService(),
        retry_policy=RetryPolicy(max_attempts=5, backoff_seconds=30, max_backoff_seconds=900),
        stale_task_timeout_seconds=1800,
        now_provider=lambda: next(clock),
    )

    async def run_test() -> None:
        task, _ = await task_store.enqueue_media(
            media,
            discovered_at=datetime(2026, 4, 9, 16, 0, tzinfo=timezone.utc),
        )
        await task_store.start_task(
            task.id,
            started_at=datetime(2026, 4, 9, 16, 0, 1, tzinfo=timezone.utc),
        )
        await task_store.mark_succeeded(
            task.id,
            completed_at=datetime(2026, 4, 9, 16, 0, 2, tzinfo=timezone.utc),
            outcome=PipelineOutcome(
                media_id=media.id,
                screenshot=ScreenshotArtifact(
                    path=Path("/tmp/ignored-stale-feishu.jpg"),
                    captured_at=media.started_at,
                ),
                analysis=AnalysisResult(
                    event_time=media.started_at,
                    event_offset_seconds=5.0,
                    elimination_type=EliminationType.POOP,
                    stool_score="4",
                    stool_shape_note="formed",
                    confidence=0.91,
                    raw_summary="Detected a poop event.",
                ),
                feishu_record_id=None,
            ),
        )
        await task_store.start_feishu_sync(
            task.id,
            started_at=datetime(2026, 4, 9, 16, 0, 3, tzinfo=timezone.utc),
        )
        processed = await worker.process_next_job()

        assert processed is not None
        assert processed.feishu_sync_status == SyncStatus.SUCCEEDED
        assert processed.feishu_record_id == "rec-123"
        assert processed.feishu_sync_attempts == 2

    asyncio.run(run_test())
