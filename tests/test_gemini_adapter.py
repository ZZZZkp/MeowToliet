from __future__ import annotations

import asyncio
from datetime import datetime
import json
from pathlib import Path

import httpx

from meow_toilet.adapters.gemini import GeminiAnalyzer
from meow_toilet.domain.entities import EliminationType, PetKitMedia
from meow_toilet.errors import ExternalServiceError


def test_gemini_analyzer_uploads_video_and_parses_structured_json(tmp_path: Path) -> None:
    video_path = tmp_path / "decoded.mp4"
    video_path.write_bytes(b"video-bytes")
    media = PetKitMedia(
        id="media-1",
        device_id="device-1",
        started_at=datetime.fromisoformat("2026-04-09T10:00:00+00:00"),
        cover_url="https://example.com/cover.jpg",
        encrypted_download_url="https://example.com/video.mp4",
        source_day="2026-04-09",
    )
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if "upload/v1beta/files" in str(request.url):
            return httpx.Response(
                200,
                headers={"X-Goog-Upload-URL": "https://upload.example.test/upload-session"},
            )
        if str(request.url) == "https://upload.example.test/upload-session":
            return httpx.Response(
                200,
                json={
                    "file": {
                        "name": "files/abc123",
                        "uri": "https://files.example.test/abc123",
                        "mimeType": "video/mp4",
                        "state": {"name": "PROCESSING"},
                    },
                },
            )
        if "v1beta/files/abc123" in str(request.url) and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "name": "files/abc123",
                    "uri": "https://files.example.test/abc123",
                    "mimeType": "video/mp4",
                    "state": {"name": "ACTIVE"},
                },
            )
        if ":generateContent" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": json.dumps(
                                            {
                                                "event_offset_seconds": 12.5,
                                                "elimination_type": "poop",
                                                "stool_shape_note": "成型，偏干燥，无明显软便",
                                                "confidence": 0.87,
                                                "raw_summary": "视频前段可见一次大便，轮廓较清楚。",
                                            },
                                        ),
                                    },
                                ],
                            },
                        },
                    ],
                },
            )
        if "v1beta/files/abc123" in str(request.url) and request.method == "DELETE":
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)

    async def run_test() -> None:
        analyzer = GeminiAnalyzer(
            api_key="test-key",
            model="gemini-2.5-flash",
            client=httpx.AsyncClient(transport=transport, timeout=10.0),
            poll_interval_seconds=0.01,
        )
        try:
            result = await analyzer.analyze_litter_video(video_path, media)
        finally:
            await analyzer.aclose()

        assert result.elimination_type == EliminationType.POOP
        assert result.event_offset_seconds == 12.5
        assert result.stool_score is None
        assert result.stool_shape_note == "成型，偏干燥，无明显软便"
        assert result.confidence == 0.87
        assert result.event_time.isoformat() == "2026-04-09T10:00:12.500000+00:00"
        assert any(":generateContent" in url for _method, url in calls)
        assert any(method == "DELETE" for method, _url in calls)

    asyncio.run(run_test())


def test_gemini_analyzer_raises_structured_error_for_invalid_json_payload(tmp_path: Path) -> None:
    video_path = tmp_path / "decoded.mp4"
    video_path.write_bytes(b"video-bytes")
    media = PetKitMedia(
        id="media-2",
        device_id="device-1",
        started_at=datetime.fromisoformat("2026-04-09T10:00:00+00:00"),
        cover_url="https://example.com/cover.jpg",
        encrypted_download_url="https://example.com/video.mp4",
        source_day="2026-04-09",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "upload/v1beta/files" in str(request.url):
            return httpx.Response(
                200,
                headers={"X-Goog-Upload-URL": "https://upload.example.test/upload-session"},
            )
        if str(request.url) == "https://upload.example.test/upload-session":
            return httpx.Response(
                200,
                json={
                    "file": {
                        "name": "files/abc123",
                        "uri": "https://files.example.test/abc123",
                        "mimeType": "video/mp4",
                        "state": {"name": "PROCESSING"},
                    },
                },
            )
        if "v1beta/files/abc123" in str(request.url) and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "name": "files/abc123",
                    "uri": "https://files.example.test/abc123",
                    "mimeType": "video/mp4",
                    "state": {"name": "ACTIVE"},
                },
            )
        if ":generateContent" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": "definitely-not-json",
                                    },
                                ],
                            },
                        },
                    ],
                },
            )
        if "v1beta/files/abc123" in str(request.url) and request.method == "DELETE":
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)

    async def run_test() -> None:
        analyzer = GeminiAnalyzer(
            api_key="test-key",
            model="gemini-2.5-flash",
            client=httpx.AsyncClient(transport=transport, timeout=10.0),
            poll_interval_seconds=0.01,
        )
        try:
            try:
                await analyzer.analyze_litter_video(video_path, media)
            except ExternalServiceError as exc:
                assert exc.kind == "response_format"
                assert exc.retryable is True
            else:
                raise AssertionError("Expected ExternalServiceError for invalid Gemini payload.")
        finally:
            await analyzer.aclose()

    asyncio.run(run_test())


def test_gemini_analyzer_falls_back_to_chinese_key_value_response(tmp_path: Path) -> None:
    video_path = tmp_path / "decoded.mp4"
    video_path.write_bytes(b"video-bytes")
    media = PetKitMedia(
        id="media-1",
        device_id="device-1",
        started_at=datetime.fromisoformat("2026-04-09T10:00:00+00:00"),
        cover_url="https://example.com/cover.jpg",
        encrypted_download_url="https://example.com/video.mp4",
        source_day="2026-04-09",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "upload/v1beta/files" in str(request.url):
            return httpx.Response(
                200,
                headers={"X-Goog-Upload-URL": "https://upload.example.test/upload-session"},
            )
        if str(request.url) == "https://upload.example.test/upload-session":
            return httpx.Response(
                200,
                json={
                    "file": {
                        "name": "files/abc123",
                        "uri": "https://files.example.test/abc123",
                        "mimeType": "video/mp4",
                        "state": {"name": "PROCESSING"},
                    },
                },
            )
        if "v1beta/files/abc123" in str(request.url) and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "name": "files/abc123",
                    "uri": "https://files.example.test/abc123",
                    "mimeType": "video/mp4",
                    "state": {"name": "ACTIVE"},
                },
            )
        if ":generateContent" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": (
                                            "排泄时间：00:12.5\n"
                                            "排泄类型：小便\n"
                                            "大便状态：不适用\n"
                                            "置信度：0.61\n"
                                            "总结：视频中可见一次小便，大便情况不适用。"
                                        ),
                                    },
                                ],
                            },
                        },
                    ],
                },
            )
        if "v1beta/files/abc123" in str(request.url) and request.method == "DELETE":
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)

    async def run_test() -> None:
        analyzer = GeminiAnalyzer(
            api_key="test-key",
            model="gemini-2.5-flash",
            client=httpx.AsyncClient(transport=transport, timeout=10.0),
            poll_interval_seconds=0.01,
        )
        try:
            result = await analyzer.analyze_litter_video(video_path, media)
        finally:
            await analyzer.aclose()

        assert result.elimination_type == EliminationType.PEE
        assert result.event_offset_seconds == 12.5
        assert result.stool_shape_note is None
        assert result.confidence == 0.61
        assert result.raw_summary == "视频中可见一次小便，大便情况不适用。"

    asyncio.run(run_test())
