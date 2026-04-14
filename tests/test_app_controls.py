from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from meow_toilet.app.dependencies import (
    get_dashboard_media_service,
    get_dashboard_snapshot_service,
    get_dashboard_video_service,
    get_manual_operations_service,
)
from meow_toilet.app.main import app
from meow_toilet.domain.entities import (
    DashboardSnapshot,
    IntegrationSnapshot,
    JobStatus,
    MediaTask,
    PetKitMedia,
    QueueSnapshot,
    SchedulerPollResult,
    SyncStatus,
)
from meow_toilet.services.dashboard import DashboardVideoAsset


class FakeSnapshotService:
    async def build_snapshot(self, *, recent_limit: int = 8) -> DashboardSnapshot:
        return DashboardSnapshot(
            generated_at=datetime(2026, 4, 10, 8, 0, tzinfo=UTC),
            integration=IntegrationSnapshot(
                petkit_ready=True,
                gemini_ready=True,
                feishu_ready=True,
            ),
            queue=QueueSnapshot(
                total=1,
                queued=1,
                running=0,
                succeeded=0,
                failed=0,
            ),
            poll_interval_seconds=300,
            check_interval_seconds=600,
            recent_tasks=[],
        )


class FakeOperationsService:
    def __init__(self) -> None:
        self.poll_calls: list[str | None] = []
        self.process_task_calls: list[str] = []
        self.process_next_calls = 0

    async def poll_once(self, *, source_day: str | None = None) -> SchedulerPollResult:
        self.poll_calls.append(source_day)
        return SchedulerPollResult(
            source_day=source_day or "2026-04-10",
            scanned_device_count=2,
            discovered_media_count=3,
            enqueued_task_count=2,
            deduped_task_count=1,
            dispatched_task_count=0,
        )

    async def process_next_task(self) -> MediaTask:
        self.process_next_calls += 1
        return self._build_task(task_id="device-1:media-next")

    async def process_task(self, task_id: str) -> MediaTask:
        self.process_task_calls.append(task_id)
        return self._build_task(task_id=task_id)

    @staticmethod
    def _build_task(task_id: str) -> MediaTask:
        media = PetKitMedia(
            id=task_id.split(":", maxsplit=1)[1],
            device_id="device-1",
            started_at=datetime(2026, 4, 10, 7, 55, tzinfo=UTC),
            cover_url=None,
            encrypted_download_url="https://example.com/video.mp4",
            source_day="2026-04-10",
        )
        return MediaTask(
            id=task_id,
            media=media,
            status=JobStatus.SUCCEEDED,
            discovered_at=datetime(2026, 4, 10, 7, 56, tzinfo=UTC),
            updated_at=datetime(2026, 4, 10, 7, 57, tzinfo=UTC),
            attempts=1,
            feishu_record_id="rec-123",
        )


class FakeMediaService:
    def __init__(self, asset: tuple[bytes, str] | None) -> None:
        self.asset = asset
        self.calls: list[str] = []

    async def load_cover_asset(self, task_id: str) -> tuple[bytes, str] | None:
        self.calls.append(task_id)
        return self.asset


class FakeVideoService:
    def __init__(
        self,
        *,
        task: MediaTask | None,
        asset: DashboardVideoAsset | None,
    ) -> None:
        self.task = task
        self.asset = asset
        self.task_calls: list[str] = []
        self.asset_calls: list[str] = []

    async def get_task(self, task_id: str) -> MediaTask | None:
        self.task_calls.append(task_id)
        return self.task

    async def load_video_asset(self, task_id: str) -> DashboardVideoAsset | None:
        self.asset_calls.append(task_id)
        return self.asset

    async def aclose(self) -> None:
        return None


def test_operations_api_routes_return_action_results_and_snapshot(tmp_path) -> None:
    snapshot_service = FakeSnapshotService()
    operations_service = FakeOperationsService()
    media_service = FakeMediaService((b"fake-cover", "image/jpeg"))
    video_file = tmp_path / "video.mp4"
    video_file.write_bytes(b"fake-video")
    video_service = FakeVideoService(
        task=operations_service._build_task(task_id="device-1:media-77"),
        asset=DashboardVideoAsset(
            path=video_file,
            media_type="video/mp4",
            prepared_at=datetime(2026, 4, 10, 8, 0, tzinfo=UTC),
        ),
    )
    app.dependency_overrides[get_dashboard_snapshot_service] = lambda: snapshot_service
    app.dependency_overrides[get_manual_operations_service] = lambda: operations_service
    app.dependency_overrides[get_dashboard_media_service] = lambda: media_service
    app.dependency_overrides[get_dashboard_video_service] = lambda: video_service

    try:
        client = TestClient(app)

        poll_response = client.post("/api/operations/poll", params={"source_day": "2026-04-09"})
        process_next_response = client.post("/api/operations/process-next")
        process_task_response = client.post("/api/operations/process/device-1:media-77")
        cover_response = client.get("/api/media/cover/device-1:media-77")
        player_response = client.get("/tasks/device-1:media-77/player")
        video_response = client.get("/api/media/video/device-1:media-77")

        assert poll_response.status_code == 200
        assert poll_response.json()["result"]["source_day"] == "2026-04-09"
        assert poll_response.json()["snapshot"]["queue"]["queued"] == 1

        assert process_next_response.status_code == 200
        assert process_next_response.json()["result"]["task_id"] == "device-1:media-next"

        assert process_task_response.status_code == 200
        assert process_task_response.json()["result"]["task_id"] == "device-1:media-77"
        assert cover_response.status_code == 200
        assert cover_response.content == b"fake-cover"
        assert cover_response.headers["content-type"] == "image/jpeg"
        assert cover_response.headers["cache-control"] == "no-store, max-age=0"
        assert cover_response.headers["pragma"] == "no-cache"
        assert player_response.status_code == 200
        assert "/api/media/video/device-1:media-77" in player_response.text
        assert "缓存文件会在下载后 24 小时内自动清理" in player_response.text
        assert video_response.status_code == 200
        assert video_response.content == b"fake-video"
        assert video_response.headers["content-type"] == "video/mp4"
        assert video_response.headers["cache-control"] == "no-store, max-age=0"
        assert video_response.headers["pragma"] == "no-cache"

        assert operations_service.poll_calls == ["2026-04-09"]
        assert operations_service.process_next_calls == 1
        assert operations_service.process_task_calls == ["device-1:media-77"]
        assert media_service.calls == ["device-1:media-77"]
        assert video_service.task_calls == ["device-1:media-77"]
        assert video_service.asset_calls == ["device-1:media-77"]
    finally:
        app.dependency_overrides.clear()


def test_cover_api_returns_404_when_cover_is_unavailable() -> None:
    app.dependency_overrides[get_dashboard_media_service] = lambda: FakeMediaService(None)

    try:
        client = TestClient(app)
        response = client.get("/api/media/cover/missing-task", follow_redirects=False)

        assert response.status_code == 404
        assert response.json() == {"detail": "Cover not found."}
    finally:
        app.dependency_overrides.clear()


def test_video_routes_return_404_when_task_or_video_is_unavailable() -> None:
    missing_video_service = FakeVideoService(task=None, asset=None)
    app.dependency_overrides[get_dashboard_video_service] = lambda: missing_video_service

    try:
        client = TestClient(app)
        player_response = client.get("/tasks/missing-task/player", follow_redirects=False)
        video_response = client.get("/api/media/video/missing-task", follow_redirects=False)

        assert player_response.status_code == 404
        assert player_response.json() == {"detail": "Task not found."}
        assert video_response.status_code == 404
        assert video_response.json() == {"detail": "Video not found."}
    finally:
        app.dependency_overrides.clear()


def test_dashboard_page_renders_cache_busting_cover_url_and_fallback_metadata() -> None:
    sample_media = PetKitMedia(
        id="media-77",
        device_id="device-1",
        started_at=datetime(2026, 4, 10, 7, 55, tzinfo=UTC),
        cover_url="https://example.com/cover.jpg",
        encrypted_download_url="https://example.com/video.mp4",
        source_day="2026-04-10",
        pet_name="翠饼",
    )
    sample_task = MediaTask(
        id="device-1:media-77",
        media=sample_media,
        status=JobStatus.SUCCEEDED,
        discovered_at=datetime(2026, 4, 10, 7, 56, tzinfo=UTC),
        updated_at=datetime(2026, 4, 10, 7, 57, tzinfo=UTC),
        attempts=2,
        feishu_sync_status=SyncStatus.PENDING,
        feishu_sync_attempts=1,
        feishu_sync_next_attempt_at=datetime(2026, 4, 10, 8, 5, tzinfo=UTC),
        event_time=datetime(2026, 4, 10, 7, 55, 12, tzinfo=UTC),
        raw_summary="Gemini says this looks like a short pee event.",
    )

    class SnapshotWithTaskService:
        async def build_snapshot(self, *, recent_limit: int = 8) -> DashboardSnapshot:
            return DashboardSnapshot(
                generated_at=datetime(2026, 4, 10, 8, 0, tzinfo=UTC),
                integration=IntegrationSnapshot(
                    petkit_ready=True,
                    gemini_ready=True,
                    feishu_ready=True,
                ),
                queue=QueueSnapshot(
                    total=1,
                    queued=0,
                    running=0,
                    succeeded=1,
                    failed=0,
                ),
                poll_interval_seconds=300,
                check_interval_seconds=600,
                recent_tasks=[sample_task],
            )

    app.dependency_overrides[get_dashboard_snapshot_service] = lambda: SnapshotWithTaskService()

    try:
        client = TestClient(app)
        response = client.get("/")

        assert response.status_code == 200
        assert 'src="/api/media/cover/device-1:media-77?ts=1775808000"' in response.text
        assert 'data-device-id="device-1"' in response.text
        assert 'data-task-id="device-1:media-77"' in response.text
        assert "分析 succeeded" in response.text
        assert "飞书 pending" in response.text
        assert "分析尝试 2 次" in response.text
        assert "飞书尝试 1 次" in response.text
        assert "发现时间 2026-04-10 15:56:00" in response.text
        assert "事件时间 2026-04-10 15:55:12" in response.text
        assert "飞书重试 2026-04-10 16:05:00" in response.text
        assert "猫 翠饼" in response.text
        assert "查看 Gemini 摘要" in response.text
        assert "Gemini says this looks like a short pee event." in response.text
        assert "buildCoverPlaceholder" in response.text
        assert "openTaskPlayer" in response.text
        assert "新窗口播放" in response.text
        assert 'const dashboardTimezone = "Asia/Shanghai";' in response.text
    finally:
        app.dependency_overrides.clear()
