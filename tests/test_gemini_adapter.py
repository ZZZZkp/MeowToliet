from __future__ import annotations

import asyncio
from datetime import datetime
import json
from pathlib import Path

import httpx

from meow_toilet.adapters.gemini import GeminiAnalyzer
from meow_toilet.domain.entities import EliminationType, PetKitMedia


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
                                                "stool_score": "4",
                                                "stool_shape_note": "formed log",
                                                "confidence": 0.87,
                                                "raw_summary": "A poop event is visible near the beginning of the clip.",
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
        assert result.stool_score == "4"
        assert result.confidence == 0.87
        assert result.event_time.isoformat() == "2026-04-09T10:00:12.500000+00:00"
        assert any(":generateContent" in url for _method, url in calls)
        assert any(method == "DELETE" for method, _url in calls)

    asyncio.run(run_test())
