from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx

from meow_toilet.adapters.feishu import FeishuBitableSink
from meow_toilet.domain.entities import AnalysisResult, EliminationType, PetKitMedia, ScreenshotArtifact


def test_feishu_sink_uploads_screenshot_and_creates_record(tmp_path: Path) -> None:
    screenshot_path = tmp_path / "event.jpg"
    screenshot_path.write_bytes(b"fake-image")
    media = PetKitMedia(
        id="media-1",
        device_id="device-1",
        started_at=datetime(2026, 4, 9, 0, 11, 30, tzinfo=timezone.utc),
        cover_url="https://example.com/cover.jpg",
        encrypted_download_url="https://example.com/video.mp4",
        source_day="2026-04-09",
    )
    analysis = AnalysisResult(
        event_time=datetime(2026, 4, 9, 0, 12, 15, tzinfo=timezone.utc),
        event_offset_seconds=45.0,
        elimination_type=EliminationType.POOP,
        stool_score="4",
        stool_shape_note="Single, smooth, sausage-shaped stool",
        confidence=0.9,
        raw_summary="The cat entered the litter box, eliminated a piece of poop, and then covered it before exiting.",
    )
    screenshot = ScreenshotArtifact(path=screenshot_path, captured_at=analysis.event_time)
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "tenant_access_token": "tenant-token",
                },
            )
        if request.url.path.endswith("/drive/v1/medias/upload_all"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "file_token": "img-token",
                    },
                },
            )
        if request.url.path.endswith("/bitable/v1/apps/app-token/tables/tbl123/records"):
            payload = request.read().decode("utf-8")
            assert "media-1" in payload
            assert "img-token" in payload
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "record": {
                            "record_id": "rec123",
                        },
                    },
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)

    async def run_test() -> None:
        sink = FeishuBitableSink(
            app_id="cli_app",
            app_secret="secret",
            app_token="app-token",
            table_id="tbl123",
            field_mapping={
                "media_id": "Media ID",
                "device_id": "Device ID",
                "event_time": "Event Time",
                "elimination_type": "Elimination Type",
                "stool_score": "Stool Score",
                "stool_shape_note": "Stool Shape Note",
                "confidence": "Confidence",
                "raw_summary": "Raw Summary",
                "screenshot": "Screenshot",
                "source_day": "Source Day",
            },
            client=httpx.AsyncClient(transport=transport, timeout=10.0),
        )
        try:
            record_id = await sink.upsert_event(media=media, analysis=analysis, screenshot=screenshot)
        finally:
            await sink.aclose()

        assert record_id == "rec123"
        assert any("/auth/v3/tenant_access_token/internal" in url for _method, url in calls)
        assert any("/drive/v1/medias/upload_all" in url for _method, url in calls)
        assert any("/bitable/v1/apps/app-token/tables/tbl123/records" in url for _method, url in calls)

    asyncio.run(run_test())
