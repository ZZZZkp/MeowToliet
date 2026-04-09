from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from pypetkitapi.media import MediaCloud

from meow_toilet.adapters.petkit import PetKitApiAdapter


def test_build_historical_record_params_uses_requested_local_day() -> None:
    adapter = PetKitApiAdapter(
        username="user@example.com",
        password="secret",
        region="cn",
        timezone_name="Asia/Shanghai",
    )

    params = adapter._build_historical_record_params(
        device_id=123,
        device_type="t6",
        type_code=9,
        source_day="2026-04-08",
    )

    expected = int(
        datetime(2026, 4, 8, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp(),
    )
    assert params == {"timestamp": expected, "deviceId": 123, "type": 9}


def test_map_media_cloud_keeps_cover_and_event_metadata() -> None:
    adapter = PetKitApiAdapter(
        username="user@example.com",
        password="secret",
        region="cn",
        timezone_name="Asia/Shanghai",
    )
    cloud_media = MediaCloud(
        event_id="device-1_1712664000",
        event_type=SimpleNamespace(value="toileting"),
        device_id=1001,
        user_id=42,
        image="https://example.com/cover.jpg",
        video="https://example.com/video.m3u8",
        filepath="1001/20260409/toileting",
        aes_key="test-key",
        timestamp=1712664000,
    )

    media = adapter._map_media_cloud(cloud_media, "2026-04-09")

    assert media.id == "device-1_1712664000"
    assert media.device_id == "1001"
    assert media.cover_url == "https://example.com/cover.jpg"
    assert media.encrypted_download_url == "https://example.com/video.m3u8"
    assert media.source_day == "2026-04-09"
    assert media.started_at.tzinfo == ZoneInfo("Asia/Shanghai")
