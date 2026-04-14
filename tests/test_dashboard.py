from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from meow_toilet.config import Settings
from meow_toilet.domain.entities import (
    AnalysisResult,
    EliminationType,
    JobStatus,
    MediaTask,
    PetKitMedia,
    PipelineOutcome,
    ScreenshotArtifact,
)
from meow_toilet.scheduler.service import SchedulerHeartbeat
from meow_toilet.services.dashboard import (
    DashboardMediaService,
    DashboardSnapshotService,
    DashboardVideoService,
    format_dashboard_datetime,
)
from meow_toilet.services.retries import RetryPolicy
from meow_toilet.services.task_store import InMemoryMediaTaskStore
from meow_toilet.workers.jobs import MediaJobWorker


class SuccessfulPipeline:
    async def run(self, request) -> PipelineOutcome:
        analysis = AnalysisResult(
            event_time=request.media.started_at,
            event_offset_seconds=3.0,
            elimination_type=EliminationType.POOP,
            stool_score="4",
            stool_shape_note="compact",
            confidence=0.88,
            raw_summary="Poop event found.",
        )
        return PipelineOutcome(
            media_id=request.media.id,
            screenshot=ScreenshotArtifact(
                path=Path("/tmp/dashboard.jpg"),
                captured_at=request.media.started_at,
            ),
            analysis=analysis,
            feishu_record_id=None,
        )


class SuccessfulFeishuSyncService:
    async def sync_task(self, task: MediaTask) -> str | None:
        return "rec-dashboard"


class FakeDashboardPetKitGateway:
    def __init__(self) -> None:
        self.download_calls: list[str] = []
        self.closed = False

    async def download_media(self, media: PetKitMedia, destination: Path) -> Path:
        self.download_calls.append(media.dedupe_key)
        destination.write_bytes(b"encrypted-video")
        return destination

    async def aclose(self) -> None:
        self.closed = True


class FakeDashboardVideoProcessor:
    def __init__(self) -> None:
        self.decode_calls: list[Path] = []

    async def decode(self, encrypted_video: Path) -> Path:
        self.decode_calls.append(encrypted_video)
        decoded_path = encrypted_video.with_name("decoded.mp4")
        decoded_path.write_bytes(b"decoded-video")
        return decoded_path


def test_format_dashboard_datetime_converts_to_asia_shanghai() -> None:
    value = datetime(2026, 4, 9, 8, 3, 4, tzinfo=UTC)

    assert format_dashboard_datetime(value, timezone_name="Asia/Shanghai") == "2026-04-09 16:03:04"


def test_dashboard_snapshot_service_reports_queue_and_recent_tasks() -> None:
    sample_media = PetKitMedia(
        id="media-dashboard-1",
        device_id="device-1",
        started_at=datetime(2026, 4, 9, 7, 58, tzinfo=UTC),
        cover_url="https://example.com/cover-dashboard.jpg",
        encrypted_download_url="https://example.com/video-dashboard.mp4",
        source_day="2026-04-09",
    )
    task_store = InMemoryMediaTaskStore()
    clock = iter(
        [
            datetime(2026, 4, 9, 8, 0, tzinfo=UTC),
            datetime(2026, 4, 9, 8, 1, tzinfo=UTC),
            datetime(2026, 4, 9, 8, 2, tzinfo=UTC),
            datetime(2026, 4, 9, 8, 3, tzinfo=UTC),
            datetime(2026, 4, 9, 8, 4, tzinfo=UTC),
            datetime(2026, 4, 9, 8, 5, tzinfo=UTC),
            datetime(2026, 4, 9, 8, 6, tzinfo=UTC),
        ],
    )

    async def seed_store() -> None:
        await task_store.enqueue_media(
            sample_media,
            discovered_at=datetime(2026, 4, 9, 7, 59, tzinfo=UTC),
        )
        worker = MediaJobWorker(
            task_store=task_store,
            pipeline=SuccessfulPipeline(),
            feishu_sync_service=SuccessfulFeishuSyncService(),
            retry_policy=RetryPolicy(max_attempts=5, backoff_seconds=30, max_backoff_seconds=900),
            stale_task_timeout_seconds=1800,
            now_provider=lambda: next(clock),
        )
        await worker.process_next_job()
        await worker.process_next_job()

    asyncio.run(seed_store())

    snapshot_service = DashboardSnapshotService(
        task_store=task_store,
        settings_provider=lambda: Settings(
            petkit_email="user@example.com",
            petkit_password="secret",
            gemini_api_key="gem-key",
            feishu_app_id="app-id",
            feishu_app_secret="app-secret",
            feishu_bitable_app_token="bitable-token",
            feishu_bitable_table_id="table-id",
        ),
        heartbeat_provider=lambda: SchedulerHeartbeat(
            poll_interval_seconds=300,
            check_interval_seconds=600,
        ),
        now_provider=lambda: datetime(2026, 4, 9, 8, 3, tzinfo=UTC),
    )

    async def run_assertions() -> None:
        snapshot = await snapshot_service.build_snapshot()

        assert snapshot.integration.petkit_ready is True
        assert snapshot.integration.gemini_ready is True
        assert snapshot.integration.feishu_ready is True
        assert snapshot.queue.succeeded == 1
        assert snapshot.queue.total == 1
        assert snapshot.poll_interval_seconds == 300
        assert snapshot.check_interval_seconds == 600
        assert snapshot.recent_tasks[0].media.id == sample_media.id
        assert snapshot.recent_tasks[0].status == JobStatus.SUCCEEDED
        assert snapshot.recent_tasks[0].feishu_record_id == "rec-dashboard"
        assert snapshot.recent_tasks[0].stool_shape_note == "compact"

    asyncio.run(run_assertions())


def test_dashboard_media_service_reads_persisted_preview_from_database(tmp_path: Path) -> None:
    sample_media = PetKitMedia(
        id="media-dashboard-2",
        device_id="device-2",
        started_at=datetime(2026, 4, 9, 9, 58, tzinfo=UTC),
        cover_url="https://example.com/expired-cover.jpg",
        encrypted_download_url="https://example.com/video-dashboard-2.mp4",
        source_day="2026-04-09",
    )
    task_store = InMemoryMediaTaskStore()
    preview_path = tmp_path / "preview.jpg"
    preview_path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

    async def run_test() -> None:
        await task_store.enqueue_media(
            sample_media,
            discovered_at=datetime(2026, 4, 9, 9, 59, tzinfo=UTC),
            preview_path=preview_path,
        )
        service = DashboardMediaService(
            task_store=task_store,
            settings_provider=lambda: Settings(
                petkit_email="user@example.com",
                petkit_password="secret",
            ),
        )
        asset = await service.load_cover_asset("device-2:media-dashboard-2")
        assert asset is not None
        content, media_type = asset
        assert content == b"\xff\xd8\xff\xe0fake-jpeg"
        assert media_type == "image/jpeg"

    asyncio.run(run_test())


def test_dashboard_media_service_returns_placeholder_when_preview_is_missing() -> None:
    sample_media = PetKitMedia(
        id="media-dashboard-3",
        device_id="device-3",
        started_at=datetime(2026, 4, 9, 10, 58, tzinfo=UTC),
        cover_url="https://example.com/not-an-image.bin",
        encrypted_download_url="https://example.com/video-dashboard-3.mp4",
        source_day="2026-04-09",
    )
    task_store = InMemoryMediaTaskStore()

    async def run_test() -> None:
        await task_store.enqueue_media(
            sample_media,
            discovered_at=datetime(2026, 4, 9, 10, 59, tzinfo=UTC),
        )
        service = DashboardMediaService(
            task_store=task_store,
            settings_provider=lambda: Settings(
                petkit_email="user@example.com",
                petkit_password="secret",
            ),
        )
        asset = await service.load_cover_asset("device-3:media-dashboard-3")

        assert asset is not None
        content, media_type = asset
        assert media_type == "image/svg+xml"
        assert "PetKit 预览暂不可用".encode() in content

    asyncio.run(run_test())


def test_dashboard_media_service_returns_placeholder_when_persisted_preview_is_invalid(
    tmp_path: Path,
) -> None:
    sample_media = PetKitMedia(
        id="media-dashboard-4",
        device_id="device-4",
        started_at=datetime(2026, 4, 9, 11, 58, tzinfo=UTC),
        cover_url="https://example.com/encrypted-cover.bin",
        encrypted_download_url="https://example.com/video-dashboard-4.mp4",
        source_day="2026-04-09",
    )
    task_store = InMemoryMediaTaskStore()
    preview_path = tmp_path / "preview.bin"
    preview_path.write_bytes(b"not-an-image")

    async def run_test() -> None:
        await task_store.enqueue_media(
            sample_media,
            discovered_at=datetime(2026, 4, 9, 11, 59, tzinfo=UTC),
            preview_path=preview_path,
        )
        service = DashboardMediaService(
            task_store=task_store,
            settings_provider=lambda: Settings(
                petkit_email="user@example.com",
                petkit_password="secret",
            ),
        )
        asset = await service.load_cover_asset("device-4:media-dashboard-4")

        assert asset is not None
        content, media_type = asset
        assert media_type == "image/svg+xml"
        assert "PetKit 预览暂不可用".encode() in content

    asyncio.run(run_test())


def test_dashboard_video_service_downloads_decodes_and_reuses_cached_video(tmp_path: Path) -> None:
    sample_media = PetKitMedia(
        id="media-dashboard-5",
        device_id="device-5",
        started_at=datetime(2026, 4, 9, 12, 58, tzinfo=UTC),
        cover_url="https://example.com/cover-dashboard-5.jpg",
        encrypted_download_url="https://example.com/video-dashboard-5.mp4",
        source_day="2026-04-09",
    )
    task_store = InMemoryMediaTaskStore()
    petkit = FakeDashboardPetKitGateway()
    video_processor = FakeDashboardVideoProcessor()

    async def run_test() -> None:
        await task_store.enqueue_media(
            sample_media,
            discovered_at=datetime(2026, 4, 9, 12, 59, tzinfo=UTC),
        )
        service = DashboardVideoService(
            task_store=task_store,
            petkit=petkit,
            video_processor=video_processor,
            cache_root=tmp_path / "dashboard-videos",
            now_provider=lambda: datetime(2026, 4, 9, 13, 0, tzinfo=UTC),
        )
        try:
            first = await service.load_video_asset("device-5:media-dashboard-5")
            second = await service.load_video_asset("device-5:media-dashboard-5")

            assert first is not None
            assert second is not None
            assert first.path == second.path
            assert first.path.read_bytes() == b"decoded-video"
            assert first.media_type == "video/mp4"
            assert petkit.download_calls == ["device-5:media-dashboard-5"]
            assert len(video_processor.decode_calls) == 1
        finally:
            await service.aclose()
            assert petkit.closed is True

    asyncio.run(run_test())


def test_dashboard_video_service_removes_expired_video_and_regenerates_it(tmp_path: Path) -> None:
    sample_media = PetKitMedia(
        id="media-dashboard-6",
        device_id="device-6",
        started_at=datetime(2026, 4, 9, 13, 58, tzinfo=UTC),
        cover_url="https://example.com/cover-dashboard-6.jpg",
        encrypted_download_url="https://example.com/video-dashboard-6.mp4",
        source_day="2026-04-09",
    )
    task_store = InMemoryMediaTaskStore()
    petkit = FakeDashboardPetKitGateway()
    video_processor = FakeDashboardVideoProcessor()
    current_time = datetime(2026, 4, 9, 14, 0, tzinfo=UTC)

    def now_provider() -> datetime:
        return current_time

    async def run_test() -> None:
        nonlocal current_time
        await task_store.enqueue_media(
            sample_media,
            discovered_at=datetime(2026, 4, 9, 13, 59, tzinfo=UTC),
        )
        service = DashboardVideoService(
            task_store=task_store,
            petkit=petkit,
            video_processor=video_processor,
            cache_root=tmp_path / "dashboard-videos",
            now_provider=now_provider,
        )
        try:
            first = await service.load_video_asset("device-6:media-dashboard-6")
            assert first is not None
            assert first.path.exists()

            current_time = current_time + timedelta(days=2)
            await service.cleanup_expired_videos()
            assert first.path.exists() is False

            regenerated = await service.load_video_asset("device-6:media-dashboard-6")
            assert regenerated is not None
            assert regenerated.path.exists()
            assert petkit.download_calls == [
                "device-6:media-dashboard-6",
                "device-6:media-dashboard-6",
            ]
            assert len(video_processor.decode_calls) == 2
        finally:
            await service.aclose()

    asyncio.run(run_test())
