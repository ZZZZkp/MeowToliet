from __future__ import annotations

from pathlib import Path

from meow_toilet.domain.entities import AnalysisResult, MediaTask, ScreenshotArtifact, SyncStatus
from meow_toilet.services.interfaces import FeishuSink


class FeishuSyncService:
    def __init__(self, *, feishu: FeishuSink) -> None:
        self._feishu = feishu

    async def sync_task(self, task: MediaTask) -> str | None:
        if task.feishu_sync_status == SyncStatus.SUCCEEDED and task.feishu_record_id:
            return task.feishu_record_id
        if task.event_time is None or task.elimination_type is None:
            raise ValueError(f"Task {task.id} is missing analysis data for Feishu sync.")
        if task.screenshot_path is None:
            raise ValueError(f"Task {task.id} is missing persisted screenshot path.")
        screenshot_path = Path(task.screenshot_path)
        if not screenshot_path.exists():
            raise FileNotFoundError(f"Persisted screenshot for task {task.id} is missing.")

        analysis = AnalysisResult(
            event_time=task.event_time,
            event_offset_seconds=(task.event_time - task.media.started_at).total_seconds(),
            elimination_type=task.elimination_type,
            stool_score=task.stool_score,
            stool_shape_note=task.stool_shape_note,
            confidence=task.confidence or 0.0,
            raw_summary=task.raw_summary or "",
        )
        screenshot = ScreenshotArtifact(
            path=screenshot_path,
            captured_at=task.event_time,
        )
        return await self._feishu.upsert_event(task.media, analysis, screenshot)
