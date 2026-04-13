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
from meow_toilet.errors import ExternalServiceError


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
        pet_name="翠饼",
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
                                "field_id": "fld-pet",
                                "field_name": "Pet Name",
                                "type": 3,
                                "property": {
                                    "options": [
                                        {"id": "opt-pet-1", "name": "翠饼"},
                                        {"id": "opt-pet-2", "name": "场长"},
                                    ],
                                },
                                "ui_type": "SingleSelect",
                            },
                            {
                                "field_id": "fld-elimination",
                                "field_name": "Elimination Type",
                                "type": 3,
                                "property": {
                                    "options": [
                                        {"id": "opt-poop", "name": "大便"},
                                        {"id": "opt-pee", "name": "小便"},
                                    ],
                                },
                                "ui_type": "SingleSelect",
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
        if (
            request.method == "GET"
            and request.url.path.endswith("/bitable/v1/apps/app-token/tables/tbl123/records")
        ):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [],
                        "has_more": False,
                    },
                },
            )
        if (
            request.method == "POST"
            and request.url.path.endswith("/bitable/v1/apps/app-token/tables/tbl123/records")
        ):
            payload = request.read().decode("utf-8")
            assert "media-1" in payload
            assert "img-token" in payload
            assert '"Pet Name":"翠饼"' in payload
            assert '"Elimination Type":"大便"' in payload
            assert "翠饼" in payload
            assert "大便" in payload
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
                "pet_name": "Pet Name",
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
        assert any(
            method == "GET" and "/bitable/v1/apps/app-token/tables/tbl123/records" in url
            for method, url in calls
        )

    asyncio.run(run_test())


def test_feishu_sink_updates_known_record_id_instead_of_creating(tmp_path: Path) -> None:
    screenshot_path = tmp_path / "event.jpg"
    screenshot_path.write_bytes(b"fake-image")
    media = PetKitMedia(
        id="media-1",
        device_id="device-1",
        started_at=datetime(2026, 4, 9, 0, 11, 30, tzinfo=UTC),
        cover_url="https://example.com/cover.jpg",
        encrypted_download_url="https://example.com/video.mp4",
        source_day="2026-04-09",
        pet_name="翠饼",
    )
    analysis = AnalysisResult(
        event_time=datetime(2026, 4, 9, 0, 12, 15, tzinfo=UTC),
        event_offset_seconds=45.0,
        elimination_type=EliminationType.PEE,
        stool_score=None,
        stool_shape_note="尿团明显",
        confidence=0.88,
        raw_summary="The cat entered, peed, and left the litter box.",
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
                            {
                                "field_id": "fld-elimination",
                                "field_name": "Elimination Type",
                                "type": 3,
                                "property": {
                                    "options": [
                                        {"id": "opt-poop", "name": "大便"},
                                        {"id": "opt-pee", "name": "小便"},
                                    ],
                                },
                                "ui_type": "SingleSelect",
                            },
                            {"field_id": "fld-shot", "field_name": "Screenshot", "type": 17},
                        ],
                    },
                },
            )
        if request.url.path.endswith("/bitable/v1/apps/app-token/tables/tbl123/records/rec-known"):
            payload = request.read().decode("utf-8")
            assert '"Elimination Type":"小便"' in payload
            assert "img-token" in payload
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "record": {
                            "record_id": "rec-known",
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
                "pet_name": "Pet Name",
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
                existing_record_id="rec-known",
            )
        finally:
            await sink.aclose()

        assert record_id == "rec-known"
        assert any(
            method == "PUT" and url.endswith("/records/rec-known")
            for method, url in calls
        )
        assert not any(
            method == "POST" and url.endswith("/tables/tbl123/records")
            for method, url in calls
        )
        assert not any(
            method == "GET" and url.endswith("/tables/tbl123/records?page_size=200")
            for method, url in calls
        )

    asyncio.run(run_test())


def test_feishu_sink_updates_existing_record_found_by_media_id(tmp_path: Path) -> None:
    screenshot_path = tmp_path / "event.jpg"
    screenshot_path.write_bytes(b"fake-image")
    media = PetKitMedia(
        id="media-1",
        device_id="device-1",
        started_at=datetime(2026, 4, 9, 0, 11, 30, tzinfo=UTC),
        cover_url="https://example.com/cover.jpg",
        encrypted_download_url="https://example.com/video.mp4",
        source_day="2026-04-09",
        pet_name="翠饼",
    )
    analysis = AnalysisResult(
        event_time=datetime(2026, 4, 9, 0, 12, 15, tzinfo=UTC),
        event_offset_seconds=45.0,
        elimination_type=EliminationType.BOTH,
        stool_score="3",
        stool_shape_note="先尿后便",
        confidence=0.93,
        raw_summary="The cat peed and pooped during the same visit.",
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
                            {"field_id": "fld-media", "field_name": "eventId", "type": 1},
                            {
                                "field_id": "fld-elimination",
                                "field_name": "排泄类型",
                                "type": 3,
                                "property": {
                                    "options": [
                                        {"id": "opt-poop", "name": "大便"},
                                        {"id": "opt-pee", "name": "小便"},
                                        {"id": "opt-both", "name": "大小便"},
                                    ],
                                },
                                "ui_type": "SingleSelect",
                            },
                            {"field_id": "fld-shot", "field_name": "大便照片", "type": 17},
                        ],
                    },
                },
            )
        if (
            request.method == "GET"
            and request.url.path.endswith("/bitable/v1/apps/app-token/tables/tbl123/records")
        ):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "record_id": "rec-existing",
                                "fields": {
                                    "eventId": "media-1",
                                },
                            },
                        ],
                        "has_more": False,
                    },
                },
            )
        if request.url.path.endswith("/bitable/v1/apps/app-token/tables/tbl123/records/rec-existing"):
            payload = request.read().decode("utf-8")
            assert '"排泄类型":"大小便"' in payload
            assert "img-token" in payload
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "record": {
                            "record_id": "rec-existing",
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
                "pet_name": "Pet Name",
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

        assert record_id == "rec-existing"
        assert any(
            method == "GET" and "/tables/tbl123/records" in url
            for method, url in calls
        )
        assert any(
            method == "PUT" and url.endswith("/records/rec-existing")
            for method, url in calls
        )
        assert not any(
            method == "POST" and url.endswith("/tables/tbl123/records")
            for method, url in calls
        )

    asyncio.run(run_test())


def test_feishu_sink_raises_retryable_rate_limit_error(tmp_path: Path) -> None:
    screenshot_path = tmp_path / "event.jpg"
    screenshot_path.write_bytes(b"fake-image")
    media = PetKitMedia(
        id="media-2",
        device_id="device-1",
        started_at=datetime(2026, 4, 9, 0, 11, 30, tzinfo=UTC),
        cover_url="https://example.com/cover.jpg",
        encrypted_download_url="https://example.com/video.mp4",
        source_day="2026-04-09",
        pet_name="翠饼",
    )
    analysis = AnalysisResult(
        event_time=datetime(2026, 4, 9, 0, 12, 15, tzinfo=UTC),
        event_offset_seconds=45.0,
        elimination_type=EliminationType.POOP,
        stool_score="4",
        stool_shape_note="Single, smooth, sausage-shaped stool",
        confidence=0.9,
        raw_summary="summary",
    )
    screenshot = ScreenshotArtifact(path=screenshot_path, captured_at=analysis.event_time)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "tenant_access_token": "tenant-token",
                },
            )
        if request.url.path.endswith("/bitable/v1/apps/app-token/tables/tbl123/fields"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [],
                    },
                },
            )
        if request.url.path.endswith("/drive/v1/medias/upload_all"):
            return httpx.Response(
                200,
                json={
                    "code": 99991663,
                    "msg": "rate limit exceeded",
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
                "pet_name": "Pet Name",
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
            try:
                await sink.upsert_event(
                    media=media,
                    analysis=analysis,
                    screenshot=screenshot,
                )
            except ExternalServiceError as exc:
                assert exc.kind == "rate_limit"
                assert exc.retryable is True
            else:
                raise AssertionError("Expected ExternalServiceError for Feishu rate limit.")
        finally:
            await sink.aclose()

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
        pet_name="翠饼",
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
                            {
                                "field_id": "fld-pet",
                                "field_name": "猫",
                                "type": 3,
                                "property": {
                                    "options": [
                                        {"id": "opt-pet-1", "name": "翠饼"},
                                        {"id": "opt-pet-2", "name": "场长"},
                                    ],
                                },
                                "ui_type": "SingleSelect",
                            },
                            {
                                "field_id": "fld-type",
                                "field_name": "排泄类型",
                                "type": 3,
                                "property": {
                                    "options": [
                                        {"id": "opt-poop", "name": "大便"},
                                        {"id": "opt-pee", "name": "小便"},
                                    ],
                                },
                                "ui_type": "SingleSelect",
                            },
                            {"field_id": "fld-note", "field_name": "大便描述", "type": 1},
                            {"field_id": "fld-shot", "field_name": "大便照片", "type": 17},
                        ],
                    },
                },
            )
        if (
            request.method == "GET"
            and request.url.path.endswith("/bitable/v1/apps/app-token/tables/tbl123/records")
        ):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [],
                        "has_more": False,
                    },
                },
            )
        if (
            request.method == "POST"
            and request.url.path.endswith("/bitable/v1/apps/app-token/tables/tbl123/records")
        ):
            payload = request.read().decode("utf-8")
            record_payloads.append(payload)
            assert "eventId" in payload
            assert "时间" in payload
            assert "猫" in payload
            assert "排泄类型" in payload
            assert "大便描述" in payload
            assert "大便照片" in payload
            assert "Elimination Type" not in payload
            assert "大便" in payload
            assert "翠饼" in payload
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
                "pet_name": "Pet Name",
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


def test_feishu_sink_maps_unknown_elimination_type_to_unclear_single_select(
    tmp_path: Path,
) -> None:
    screenshot_path = tmp_path / "event.jpg"
    screenshot_path.write_bytes(b"fake-image")
    media = PetKitMedia(
        id="media-unknown",
        device_id="device-1",
        started_at=datetime(2026, 4, 9, 0, 11, 30, tzinfo=UTC),
        cover_url="https://example.com/cover.jpg",
        encrypted_download_url="https://example.com/video.mp4",
        source_day="2026-04-09",
        pet_name="翠饼",
    )
    analysis = AnalysisResult(
        event_time=datetime(2026, 4, 9, 0, 12, 15, tzinfo=UTC),
        event_offset_seconds=45.0,
        elimination_type=EliminationType.UNKNOWN,
        stool_score=None,
        stool_shape_note="看不清",
        confidence=0.42,
        raw_summary="画面遮挡严重，无法稳定判断排泄类型。",
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
                            {"field_id": "fld-time", "field_name": "时间", "type": 1},
                            {
                                "field_id": "fld-pet",
                                "field_name": "猫",
                                "type": 3,
                                "property": {
                                    "options": [
                                        {"id": "opt-pet-1", "name": "翠饼"},
                                    ],
                                },
                                "ui_type": "SingleSelect",
                            },
                            {
                                "field_id": "fld-type",
                                "field_name": "排泄类型",
                                "type": 3,
                                "property": {
                                    "options": [
                                        {"id": "opt-poop", "name": "大便"},
                                        {"id": "opt-pee", "name": "小便"},
                                        {"id": "opt-unknown", "name": "看不清"},
                                    ],
                                },
                                "ui_type": "SingleSelect",
                            },
                            {"field_id": "fld-note", "field_name": "大便描述", "type": 1},
                            {"field_id": "fld-shot", "field_name": "大便照片", "type": 17},
                        ],
                    },
                },
            )
        if (
            request.method == "GET"
            and request.url.path.endswith("/bitable/v1/apps/app-token/tables/tbl123/records")
        ):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [],
                        "has_more": False,
                    },
                },
            )
        if (
            request.method == "POST"
            and request.url.path.endswith("/bitable/v1/apps/app-token/tables/tbl123/records")
        ):
            payload = request.read().decode("utf-8")
            record_payloads.append(payload)
            assert '"排泄类型":"看不清"' in payload
            assert '"大便描述":"看不清"' in payload
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "record": {
                            "record_id": "rec-unknown",
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
                "pet_name": "Pet Name",
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

        assert record_id == "rec-unknown"
        assert len(record_payloads) == 1

    asyncio.run(run_test())
