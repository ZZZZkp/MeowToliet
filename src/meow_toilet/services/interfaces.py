from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

from meow_toilet.domain.entities import (
    AnalysisResult,
    MediaTask,
    PetKitDevice,
    PetKitMedia,
    PipelineOutcome,
    ScreenshotArtifact,
)


class PetKitGateway(Protocol):
    async def ensure_session(self) -> None: ...

    async def list_devices(self) -> list[PetKitDevice]: ...

    async def list_media(self, device: PetKitDevice, source_day: str) -> list[PetKitMedia]: ...

    async def download_media(self, media: PetKitMedia, destination: Path) -> Path: ...


class VideoProcessor(Protocol):
    async def decode(self, encrypted_video: Path) -> Path: ...

    async def capture_cover_frame(
        self,
        decoded_video: Path,
        second_offset: float,
        destination: Path,
    ) -> Path: ...


class VisionAnalyzer(Protocol):
    async def analyze_litter_video(
        self,
        decoded_video: Path,
        media: PetKitMedia,
    ) -> AnalysisResult: ...


class FeishuSink(Protocol):
    async def upsert_event(
        self,
        media: PetKitMedia,
        analysis: AnalysisResult,
        screenshot: ScreenshotArtifact,
    ) -> str | None: ...


class MediaTaskStore(Protocol):
    async def enqueue_media(
        self,
        media: PetKitMedia,
        discovered_at: datetime,
    ) -> tuple[MediaTask, bool]: ...

    async def start_task(self, task_id: str, started_at: datetime) -> MediaTask | None: ...

    async def start_next_task(self, started_at: datetime) -> MediaTask | None: ...

    async def mark_succeeded(
        self,
        task_id: str,
        completed_at: datetime,
        outcome: PipelineOutcome,
    ) -> MediaTask: ...

    async def mark_failed(self, task_id: str, failed_at: datetime, error: str) -> MediaTask: ...

    async def get_task(self, task_id: str) -> MediaTask | None: ...

    async def list_tasks(self, *, limit: int | None = None) -> list[MediaTask]: ...


class JobDispatcher(Protocol):
    async def enqueue_media_task(self, task_id: str) -> bool: ...
