from __future__ import annotations

from pathlib import Path
from typing import Protocol

from meow_toilet.domain.entities import AnalysisResult, PetKitDevice, PetKitMedia, ScreenshotArtifact


class PetKitGateway(Protocol):
    async def ensure_session(self) -> None: ...

    async def list_devices(self) -> list[PetKitDevice]: ...

    async def list_media(self, device: PetKitDevice, source_day: str) -> list[PetKitMedia]: ...

    async def download_media(self, media: PetKitMedia, destination: Path) -> Path: ...


class VideoProcessor(Protocol):
    async def decode(self, encrypted_video: Path) -> Path: ...

    async def capture_cover_frame(self, decoded_video: Path, second_offset: float, destination: Path) -> Path: ...


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
    ) -> None: ...
