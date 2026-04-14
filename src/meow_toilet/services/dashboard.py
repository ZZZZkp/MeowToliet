from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from meow_toilet.config import Settings, get_settings
from meow_toilet.domain.entities import (
    DashboardSnapshot,
    IntegrationSnapshot,
    JobStatus,
    MediaTask,
    QueueSnapshot,
)
from meow_toilet.scheduler.service import SchedulerHeartbeat, build_heartbeat
from meow_toilet.services.interfaces import MediaTaskStore, PetKitGateway, VideoProcessor

DASHBOARD_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_VIDEO_RETENTION = timedelta(days=1)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def format_dashboard_datetime(value: datetime | None, *, timezone_name: str) -> str:
    if value is None:
        return ""

    timezone = ZoneInfo(timezone_name)
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.astimezone(timezone).strftime(DASHBOARD_DATETIME_FORMAT)


@dataclass(frozen=True, slots=True)
class DashboardVideoAsset:
    path: Path
    media_type: str
    prepared_at: datetime


class DashboardSnapshotService:
    def __init__(
        self,
        *,
        task_store: MediaTaskStore,
        settings_provider: Callable[[], Settings] = get_settings,
        heartbeat_provider: Callable[[], SchedulerHeartbeat] = build_heartbeat,
        now_provider: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._task_store = task_store
        self._settings_provider = settings_provider
        self._heartbeat_provider = heartbeat_provider
        self._now_provider = now_provider

    async def build_snapshot(self, *, recent_limit: int = 8) -> DashboardSnapshot:
        settings = self._settings_provider()
        heartbeat = self._heartbeat_provider()
        all_tasks = await self._task_store.list_tasks()
        recent_tasks = all_tasks[:recent_limit]

        return DashboardSnapshot(
            generated_at=self._now_provider(),
            integration=IntegrationSnapshot(
                petkit_ready=settings.petkit_credentials_configured,
                gemini_ready=settings.gemini_configured,
                feishu_ready=settings.feishu_configured,
            ),
            queue=QueueSnapshot(
                total=len(all_tasks),
                queued=sum(task.status == JobStatus.QUEUED for task in all_tasks),
                running=sum(task.status == JobStatus.RUNNING for task in all_tasks),
                succeeded=sum(task.status == JobStatus.SUCCEEDED for task in all_tasks),
                failed=sum(task.status == JobStatus.FAILED for task in all_tasks),
            ),
            poll_interval_seconds=heartbeat.poll_interval_seconds,
            check_interval_seconds=heartbeat.check_interval_seconds,
            recent_tasks=recent_tasks,
        )


class DashboardMediaService:
    """为看板提供数据库驱动的本地预览图读取能力。"""

    def __init__(
        self,
        *,
        task_store: MediaTaskStore,
        settings_provider: Callable[[], Settings] = get_settings,
    ) -> None:
        self._task_store = task_store
        self._settings_provider = settings_provider

    async def aclose(self) -> None:
        return None

    async def load_cover_asset(self, task_id: str) -> tuple[bytes, str] | None:
        task = await self._task_store.get_task(task_id)
        if task is None:
            return None
        if task.preview_path is None or not task.preview_path.exists():
            return self._build_placeholder(task_id=task.id, device_id=task.media.device_id)

        content = task.preview_path.read_bytes()
        media_type = self._detect_image_media_type(content=content, response_media_type=None)
        if media_type is None:
            return self._build_placeholder(task_id=task.id, device_id=task.media.device_id)
        return content, media_type

    @staticmethod
    def _detect_image_media_type(content: bytes, response_media_type: str | None) -> str | None:
        normalized = (response_media_type or "").split(";", maxsplit=1)[0].strip().lower()
        if normalized.startswith("image/"):
            return normalized
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if content.startswith(b"BM"):
            return "image/bmp"
        if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            return "image/webp"
        if len(content) >= 12 and content[4:12] in {b"ftypavif", b"ftypheic", b"ftypheif"}:
            return "image/avif"
        stripped = content.lstrip()
        if stripped.startswith(b"<svg") or stripped.startswith(b"<?xml"):
            return "image/svg+xml"
        return None

    @staticmethod
    def _build_placeholder(*, task_id: str, device_id: str) -> tuple[bytes, str]:
        safe_task_id = escape(task_id)
        safe_device_id = escape(device_id)
        svg = f"""
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 360">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#d8efe8"/>
      <stop offset="100%" stop-color="#f4efe6"/>
    </linearGradient>
  </defs>
  <rect width="640" height="360" rx="28" fill="url(#bg)"/>
  <circle cx="112" cy="108" r="44" fill="#0f766e" opacity="0.14"/>
  <circle cx="530" cy="274" r="54" fill="#1f2522" opacity="0.06"/>
  <text
    x="56"
    y="170"
    font-size="34"
    fill="#1f2522"
    font-family="Avenir Next, Helvetica Neue, sans-serif"
  >
    PetKit 预览暂不可用
  </text>
  <text
    x="56"
    y="214"
    font-size="20"
    fill="#6b746e"
    font-family="Avenir Next, Helvetica Neue, sans-serif"
  >
    设备 {safe_device_id}
  </text>
  <text x="56" y="248" font-size="18" fill="#6b746e" font-family="Menlo, monospace">
    {safe_task_id}
  </text>
</svg>
""".strip()
        return svg.encode("utf-8"), "image/svg+xml"


class DashboardVideoService:
    """为看板提供按需下载、解码和短期缓存的视频回放能力。"""

    def __init__(
        self,
        *,
        task_store: MediaTaskStore,
        petkit: PetKitGateway,
        video_processor: VideoProcessor,
        cache_root: Path,
        retention: timedelta = DEFAULT_VIDEO_RETENTION,
        now_provider: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._task_store = task_store
        self._petkit = petkit
        self._video_processor = video_processor
        self._cache_root = cache_root
        self._retention = retention
        self._now_provider = now_provider
        self._locks: dict[str, asyncio.Lock] = {}
        self._deletion_tasks: dict[str, asyncio.Task[None]] = {}

    async def aclose(self) -> None:
        deletion_tasks = list(self._deletion_tasks.values())
        for task in deletion_tasks:
            task.cancel()
        if deletion_tasks:
            await asyncio.gather(*deletion_tasks, return_exceptions=True)
        self._deletion_tasks.clear()
        if hasattr(self._petkit, "aclose"):
            await self._petkit.aclose()

    async def get_task(self, task_id: str) -> MediaTask | None:
        await self.cleanup_expired_videos()
        return await self._task_store.get_task(task_id)

    async def load_video_asset(self, task_id: str) -> DashboardVideoAsset | None:
        await self.cleanup_expired_videos()
        task = await self._task_store.get_task(task_id)
        if task is None:
            return None

        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            existing_asset = self._load_cached_asset(task_id)
            if existing_asset is not None:
                self._schedule_deletion(task_id, existing_asset.path, existing_asset.prepared_at)
                return existing_asset

            self._cache_root.mkdir(parents=True, exist_ok=True)
            final_path = self._build_cache_path(task_id)
            if final_path.exists():
                final_path.unlink(missing_ok=True)

            with TemporaryDirectory(prefix=f"{self._safe_task_id(task_id)}-") as temp_root:
                workspace_root = Path(temp_root)
                downloaded_path = workspace_root / "source.mp4"
                await self._petkit.download_media(task.media, downloaded_path)
                decoded_path = await self._video_processor.decode(downloaded_path)
                shutil.move(str(decoded_path), final_path)

            prepared_at = self._now_provider()
            timestamp = prepared_at.timestamp()
            try:
                final_path.chmod(0o644)
                os.utime(final_path, (timestamp, timestamp))
            except OSError:
                pass

            asset = DashboardVideoAsset(
                path=final_path,
                media_type="video/mp4",
                prepared_at=prepared_at,
            )
            self._schedule_deletion(task_id, asset.path, asset.prepared_at)
            return asset

    async def cleanup_expired_videos(self) -> None:
        if not self._cache_root.exists():
            return

        expires_before = self._now_provider() - self._retention
        for candidate in self._cache_root.iterdir():
            if candidate.is_dir():
                prepared_at = datetime.fromtimestamp(candidate.stat().st_mtime, tz=UTC)
                if prepared_at <= expires_before:
                    shutil.rmtree(candidate, ignore_errors=True)
                continue
            if not candidate.is_file():
                continue
            prepared_at = datetime.fromtimestamp(candidate.stat().st_mtime, tz=UTC)
            if prepared_at <= expires_before:
                candidate.unlink(missing_ok=True)

    def _load_cached_asset(self, task_id: str) -> DashboardVideoAsset | None:
        candidate = self._build_cache_path(task_id)
        if not candidate.exists() or not candidate.is_file():
            return None
        prepared_at = datetime.fromtimestamp(candidate.stat().st_mtime, tz=UTC)
        if self._now_provider() - prepared_at >= self._retention:
            candidate.unlink(missing_ok=True)
            return None
        return DashboardVideoAsset(
            path=candidate,
            media_type="video/mp4",
            prepared_at=prepared_at,
        )

    def _schedule_deletion(self, task_id: str, path: Path, prepared_at: datetime) -> None:
        previous = self._deletion_tasks.get(task_id)
        if previous is not None:
            previous.cancel()

        remaining_seconds = max(
            0.0,
            (prepared_at + self._retention - self._now_provider()).total_seconds(),
        )
        self._deletion_tasks[task_id] = asyncio.create_task(
            self._delete_after_delay(
                task_id=task_id,
                path=path,
                delay_seconds=remaining_seconds,
            ),
        )

    async def _delete_after_delay(
        self,
        *,
        task_id: str,
        path: Path,
        delay_seconds: float,
    ) -> None:
        try:
            await asyncio.sleep(delay_seconds)
            lock = self._locks.setdefault(task_id, asyncio.Lock())
            async with lock:
                asset = self._load_cached_asset(task_id)
                if asset is None and path.exists():
                    path.unlink(missing_ok=True)
        except asyncio.CancelledError:
            return
        finally:
            current = self._deletion_tasks.get(task_id)
            if current is asyncio.current_task():
                self._deletion_tasks.pop(task_id, None)

    def _build_cache_path(self, task_id: str) -> Path:
        return self._cache_root / f"{self._safe_task_id(task_id)}.mp4"

    @staticmethod
    def _safe_task_id(task_id: str) -> str:
        return task_id.replace("/", "_").replace(":", "_")
