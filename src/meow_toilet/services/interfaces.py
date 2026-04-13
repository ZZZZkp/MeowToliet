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
    StaleRecoveryResult,
)


class PetKitGateway(Protocol):
    async def ensure_session(self) -> None: ...

    async def list_devices(self) -> list[PetKitDevice]: ...

    async def list_media(self, device: PetKitDevice, source_day: str) -> list[PetKitMedia]: ...

    async def download_media(self, media: PetKitMedia, destination: Path) -> Path: ...

    async def download_cover_image(self, media: PetKitMedia, destination: Path) -> Path: ...


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
        *,
        existing_record_id: str | None = None,
    ) -> str | None: ...


class MediaTaskStore(Protocol):
    async def enqueue_media(
        self,
        media: PetKitMedia,
        discovered_at: datetime,
        *,
        preview_path: Path | None = None,
    ) -> tuple[MediaTask, bool]: ...

    async def start_task(self, task_id: str, started_at: datetime) -> MediaTask | None: ...

    async def start_next_task(self, started_at: datetime) -> MediaTask | None: ...

    async def start_feishu_sync(self, task_id: str, started_at: datetime) -> MediaTask | None: ...

    async def start_next_feishu_sync(self, started_at: datetime) -> MediaTask | None: ...

    async def mark_succeeded(
        self,
        task_id: str,
        completed_at: datetime,
        outcome: PipelineOutcome,
    ) -> MediaTask: ...

    async def mark_failed(
        self,
        task_id: str,
        failed_at: datetime,
        error: str,
        *,
        error_kind: str | None = None,
        next_attempt_at: datetime | None = None,
    ) -> MediaTask: ...

    async def mark_feishu_sync_succeeded(
        self,
        task_id: str,
        synced_at: datetime,
        *,
        feishu_record_id: str | None,
    ) -> MediaTask: ...

    async def mark_feishu_sync_failed(
        self,
        task_id: str,
        failed_at: datetime,
        error: str,
        *,
        error_kind: str | None = None,
        next_attempt_at: datetime | None = None,
    ) -> MediaTask: ...

    async def get_task(self, task_id: str) -> MediaTask | None: ...

    async def list_tasks(self, *, limit: int | None = None) -> list[MediaTask]: ...

    async def recover_stale_tasks(
        self,
        *,
        stale_before: datetime,
        recovered_at: datetime,
    ) -> StaleRecoveryResult: ...


class JobDispatcher(Protocol):
    async def enqueue_media_task(self, task_id: str) -> bool: ...
