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
)
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
            feishu_record_id="rec-123",
        )


class FailingPipeline:
    async def run(self, request) -> PipelineOutcome:
        raise RuntimeError(f"boom for {request.media.id}")


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
        ],
    )
    worker = MediaJobWorker(
        task_store=task_store,
        pipeline=SuccessfulPipeline(),
        now_provider=lambda: next(clock),
    )

    async def run_test() -> None:
        await task_store.enqueue_media(media, discovered_at=datetime(2026, 4, 9, 9, 59, tzinfo=timezone.utc))
        task = await worker.process_next_job()

        assert task is not None
        assert task.status == JobStatus.SUCCEEDED
        assert task.attempts == 1
        assert task.feishu_record_id == "rec-123"
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
        ],
    )
    worker = MediaJobWorker(
        task_store=task_store,
        pipeline=FailingPipeline(),
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
