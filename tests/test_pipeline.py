from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


class FakeFeishuSink:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, Path]] = []

    async def upsert_event(
        self,
        media: PetKitMedia,
        analysis: AnalysisResult,
        screenshot,
    ) -> str:
        self.upserts.append((media.id, screenshot.path))
        return "rec-test"


def test_pipeline_uses_temporary_workspace_and_cleans_it(tmp_path: Path) -> None:
    async def run_test() -> None:
        started_at = datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc)
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
        feishu = FakeFeishuSink()
        pipeline = LitterEventPipeline(
            petkit=petkit,
            video_processor=processor,
            analyzer=analyzer,
            feishu=feishu,
            temp_store=TemporaryMediaStore(tmp_path),
        )

        outcome = await pipeline.run(PipelineRequest(media=media))

        assert outcome.media_id == "media-1"
        assert outcome.feishu_record_id == "rec-test"
        assert outcome.analysis.elimination_type == EliminationType.POOP
        assert processor.capture_calls[0][1] == 9.0
        assert feishu.upserts == [("media-1", outcome.screenshot.path)]
        assert not outcome.screenshot.path.exists()
        assert list(tmp_path.iterdir()) == []

    asyncio.run(run_test())
