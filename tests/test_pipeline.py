from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from meow_toilet.services.artifacts import PersistentArtifactStore
from meow_toilet.domain.entities import AnalysisResult, EliminationType, PetKitMedia, PipelineRequest
from meow_toilet.services.pipeline import LitterEventPipeline
from meow_toilet.services.temp_files import TemporaryMediaStore


class FakePetKitGateway:
    def __init__(self) -> None:
        self.downloads: list[Path] = []

    async def download_media(self, media: PetKitMedia, destination: Path) -> Path:
        destination.write_text("encrypted-video", encoding="utf-8")
        self.downloads.append(destination)
        return destination


class FakeVideoProcessor:
    def __init__(self) -> None:
        self.decode_calls: list[Path] = []
        self.capture_calls: list[tuple[Path, float, Path]] = []

    async def decode(self, encrypted_video: Path) -> Path:
        decoded = encrypted_video.with_name("decoded.mp4")
        decoded.write_text("decoded-video", encoding="utf-8")
        self.decode_calls.append(encrypted_video)
        return decoded

    async def capture_cover_frame(
        self,
        decoded_video: Path,
        second_offset: float,
        destination: Path,
    ) -> Path:
        destination.write_text("frame", encoding="utf-8")
        self.capture_calls.append((decoded_video, second_offset, destination))
        return destination


class FakeAnalyzer:
    def __init__(self, event_time: datetime) -> None:
        self.event_time = event_time
        self.calls: list[tuple[Path, str]] = []

    async def analyze_litter_video(self, decoded_video: Path, media: PetKitMedia) -> AnalysisResult:
        self.calls.append((decoded_video, media.id))
        return AnalysisResult(
            event_time=self.event_time,
            event_offset_seconds=9.0,
            elimination_type=EliminationType.POOP,
            stool_score="4",
            stool_shape_note="smooth and soft",
            confidence=0.93,
            raw_summary="Poop event detected near the middle of the clip.",
        )


def test_pipeline_persists_screenshot_and_cleans_temporary_workspace(tmp_path: Path) -> None:
    async def run_test() -> None:
        started_at = datetime(2026, 4, 9, 10, 0, tzinfo=UTC)
        event_time = started_at + timedelta(seconds=9)
        media = PetKitMedia(
            id="media-1",
            device_id="device-1",
            started_at=started_at,
            cover_url="https://example.com/cover.jpg",
            encrypted_download_url="https://example.com/video.enc",
            source_day="2026-04-09",
        )
        petkit = FakePetKitGateway()
        processor = FakeVideoProcessor()
        analyzer = FakeAnalyzer(event_time=event_time)
        pipeline = LitterEventPipeline(
            petkit=petkit,
            video_processor=processor,
            analyzer=analyzer,
            temp_store=TemporaryMediaStore(tmp_path),
            artifact_store=PersistentArtifactStore(tmp_path / "screenshots"),
        )

        outcome = await pipeline.run(PipelineRequest(media=media))

        assert outcome.media_id == "media-1"
        assert outcome.feishu_record_id is None
        assert outcome.analysis.elimination_type == EliminationType.POOP
        assert processor.capture_calls[0][1] == 9.0
        assert outcome.screenshot.path.exists()
        assert outcome.screenshot.path.parent.name == "screenshots"
        assert [path.name for path in tmp_path.iterdir()] == ["screenshots"]

    asyncio.run(run_test())
