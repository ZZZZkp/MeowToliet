from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp

from meow_toilet.domain.entities import PetKitMedia


@dataclass(frozen=True, slots=True)
class MediaWorkspace:
    root: Path
    encrypted_video: Path
    decoded_video: Path
    screenshot_path: Path


class TemporaryMediaStore:
    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    @asynccontextmanager
    async def allocate(self, media: PetKitMedia):
        self._base_dir.mkdir(parents=True, exist_ok=True)
        workspace_root = Path(mkdtemp(prefix=f"{media.device_id}-{media.id}-", dir=self._base_dir))
        workspace = MediaWorkspace(
            root=workspace_root,
            encrypted_video=workspace_root / "source.enc",
            decoded_video=workspace_root / "decoded.mp4",
            screenshot_path=workspace_root / "event.jpg",
        )
        try:
            yield workspace
        finally:
            shutil.rmtree(workspace_root, ignore_errors=True)

