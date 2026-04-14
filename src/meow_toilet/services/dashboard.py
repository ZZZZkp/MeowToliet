from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from html import escape
from meow_toilet.config import Settings, get_settings
from meow_toilet.domain.entities import (
    DashboardSnapshot,
    IntegrationSnapshot,
    JobStatus,
    QueueSnapshot,
)
from meow_toilet.scheduler.service import SchedulerHeartbeat, build_heartbeat
from meow_toilet.services.interfaces import MediaTaskStore


def _utc_now() -> datetime:
    return datetime.now(UTC)


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
