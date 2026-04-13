from __future__ import annotations

from pathlib import Path
import shutil

from meow_toilet.domain.entities import ScreenshotArtifact


class PersistentArtifactStore:
    def __init__(self, screenshots_root: Path) -> None:
        self._screenshots_root = screenshots_root

    async def persist_screenshot(
        self,
        *,
        task_id: str,
        source_path: Path,
        captured_at,
    ) -> ScreenshotArtifact:
        self._screenshots_root.mkdir(parents=True, exist_ok=True)
        destination = self._screenshots_root / f"{self._safe_task_id(task_id)}.jpg"
        shutil.copy2(source_path, destination)
        return ScreenshotArtifact(path=destination, captured_at=captured_at)

    @staticmethod
    def _safe_task_id(task_id: str) -> str:
        return task_id.replace("/", "_").replace(":", "_")
