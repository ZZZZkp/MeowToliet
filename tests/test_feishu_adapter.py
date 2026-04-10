from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx

from meow_toilet.adapters.feishu import FeishuBitableSink
from meow_toilet.domain.entities import (
    AnalysisResult,
    EliminationType,
    PetKitMedia,
    ScreenshotArtifact,
)


def test_feishu_sink_uploads_screenshot_and_creates_record(tmp_path: Path) -> None:
    screenshot_path = tmp_path / "event.jpg"
    screenshot_path.write_bytes(b"fake-image")
    media = PetKitMedia(
        id="media-1",
        device_id="device-1",
        started_at=datetime(2026, 4, 9, 0, 11, 30, tzinfo=UTC),
        cover_url="https://example.com/cover.jpg",
        encrypted_download_url="https://example.com/video.mp4",
        source_day="2026-04-09",
    )
    analysis = AnalysisResult(
        event_time=datetime(2026, 4, 9, 0, 12, 15, tzinfo=UTC),
        event_offset_seconds=45.0,
        elimination_type=EliminationType.POOP,
        stool_score="4",
        stool_shape_note="Single, smooth, sausage-shaped stool",
        confidence=0.9,
        raw_summary=(
            "The cat entered the litter box, eliminated a piece of poop, "
            "and then covered it before exiting."
        ),
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
        if request.url.path.endswith("/bitable/v1/apps/app-token/tables/tbl123/fields"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {"field_id": "fld-media", "field_name": "Media ID", "type": 1},
                            {"field_id": "fld-device", "field_name": "Device ID", "type": 1},
                            {"field_id": "fld-event-time", "field_name": "Event Time", "type": 5},
                            {
                                "field_id": "fld-elimination",
                                "field_name": "Elimination Type",
                                "type": 1,
                            },
                            {"field_id": "fld-stool", "field_name": "Stool Score", "type": 1},
                            {
                                "field_id": "fld-stool-note",
                                "field_name": "Stool Shape Note",
                                "type": 1,
                            },
                            {"field_id": "fld-confidence", "field_name": "Confidence", "type": 2},
                            {"field_id": "fld-summary", "field_name": "Raw Summary", "type": 1},
                            {"field_id": "fld-shot", "field_name": "Screenshot", "type": 17},
                            {"field_id": "fld-day", "field_name": "Source Day", "type": 1},
                        ],
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
            record_id = await sink.upsert_event(
                media=media,
                analysis=analysis,
                screenshot=screenshot,
            )
        finally:
            await sink.aclose()

        assert record_id == "rec123"
        assert any("/auth/v3/tenant_access_token/internal" in url for _method, url in calls)
        assert any("/drive/v1/medias/upload_all" in url for _method, url in calls)
        assert any(
            "/bitable/v1/apps/app-token/tables/tbl123/records" in url
            for _method, url in calls
        )

    asyncio.run(run_test())


def test_feishu_sink_falls_back_to_existing_chinese_field_names(tmp_path: Path) -> None:
    screenshot_path = tmp_path / "event.jpg"
    screenshot_path.write_bytes(b"fake-image")
    media = PetKitMedia(
        id="media-1",
        device_id="device-1",
        started_at=datetime(2026, 4, 9, 0, 11, 30, tzinfo=UTC),
        cover_url="https://example.com/cover.jpg",
        encrypted_download_url="https://example.com/video.mp4",
        source_day="2026-04-09",
    )
    analysis = AnalysisResult(
        event_time=datetime(2026, 4, 9, 0, 12, 15, tzinfo=UTC),
        event_offset_seconds=45.0,
        elimination_type=EliminationType.POOP,
        stool_score="4",
        stool_shape_note="Single, smooth, sausage-shaped stool",
        confidence=0.9,
        raw_summary=(
            "The cat entered the litter box, eliminated a piece of poop, "
            "and then covered it before exiting."
        ),
    )
    screenshot = ScreenshotArtifact(path=screenshot_path, captured_at=analysis.event_time)
    record_payloads: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
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
        if request.url.path.endswith("/bitable/v1/apps/app-token/tables/tbl123/fields"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {"field_id": "fld-media", "field_name": "eventId", "type": 1},
                            {"field_id": "fld-time", "field_name": "时间", "type": 5},
                            {"field_id": "fld-note", "field_name": "大便描述", "type": 1},
                            {"field_id": "fld-shot", "field_name": "大便照片", "type": 17},
                        ],
                    },
                },
            )
        if request.url.path.endswith("/bitable/v1/apps/app-token/tables/tbl123/records"):
            payload = request.read().decode("utf-8")
            record_payloads.append(payload)
            assert "eventId" in payload
            assert "时间" in payload
            assert "大便描述" in payload
            assert "大便照片" in payload
            assert "Elimination Type" not in payload
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "record": {
                            "record_id": "rec456",
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
            record_id = await sink.upsert_event(
                media=media,
                analysis=analysis,
                screenshot=screenshot,
            )
        finally:
            await sink.aclose()

        assert record_id == "rec456"
        assert len(record_payloads) == 1

    asyncio.run(run_test())
