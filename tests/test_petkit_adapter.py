from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from pypetkitapi.media import MediaCloud

from meow_toilet.adapters import petkit as petkit_module
from meow_toilet.adapters.petkit import PetKitApiAdapter
from meow_toilet.domain.entities import PetKitDevice, PetKitMedia


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

    media = adapter._map_media_cloud(cloud_media, "2026-04-09", pet_name="翠饼")

    assert media.id == "device-1_1712664000"
    assert media.device_id == "1001"
    assert media.cover_url == "https://example.com/cover.jpg"
    assert media.encrypted_download_url == "https://example.com/video.m3u8"
    assert media.source_day == "2026-04-09"
    assert media.pet_name == "翠饼"
    assert media.started_at.tzinfo == ZoneInfo("Asia/Shanghai")


def test_resolve_pet_name_for_media_matches_timestamp() -> None:
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
    records = [
        SimpleNamespace(timestamp=1712663000, pet_name="酥酥"),
        SimpleNamespace(timestamp=1712664000, pet_name="翠饼"),
    ]

    pet_name = PetKitApiAdapter._resolve_pet_name_for_media(
        media_cloud=cloud_media,
        records=records,
    )

    assert pet_name == "翠饼"


def test_download_media_can_reload_target_media_when_local_cache_is_empty(
    monkeypatch,
    tmp_path: Path,
) -> None:
    adapter = PetKitApiAdapter(
        username="user@example.com",
        password="secret",
        region="cn",
        timezone_name="Asia/Shanghai",
    )
    media = PetKitMedia(
        id="108228_1775777657",
        device_id="108228",
        started_at=datetime(2026, 4, 9, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        cover_url="https://example.com/cover.jpg",
        encrypted_download_url="https://example.com/video.m3u8",
        source_day="2026-04-09",
    )
    cloud_media = MediaCloud(
        event_id="108228_1775777657",
        event_type=SimpleNamespace(value="toileting"),
        device_id=108228,
        user_id=42,
        image="https://example.com/cover.jpg",
        video="https://example.com/video.m3u8",
        filepath="108228/20260409/toileting",
        aes_key="test-key",
        timestamp=1775777657,
    )
    entity = SimpleNamespace(
        name="客厅猫厕所",
        device_nfo=SimpleNamespace(device_id="108228", device_type="t4", type_code=9),
        device_records=[],
    )
    get_devices_calls = 0
    fetched_days: list[str] = []
    downloaded_media: list[str] = []

    async def fake_get_devices_data() -> None:
        nonlocal get_devices_calls
        get_devices_calls += 1

    async def fake_gather_all_media_from_cloud(_entities) -> list[MediaCloud]:
        return [cloud_media]

    client = SimpleNamespace(
        get_devices_data=fake_get_devices_data,
        media_manager=SimpleNamespace(gather_all_media_from_cloud=fake_gather_all_media_from_cloud),
    )

    async def fake_ensure_client():
        return client

    async def fake_fetch_litter_records_for_day(*, client, entity, source_day: str):
        del client, entity
        fetched_days.append(source_day)
        return []

    class FakeDownloader:
        def __init__(self, root: Path, client) -> None:
            self._root = root
            self._client = client

        async def download_file(self, media_cloud: MediaCloud, media_types) -> None:
            del media_types
            assert self._client is client
            downloaded_media.append(media_cloud.event_id)
            filename = f"{media_cloud.device_id}_{media_cloud.timestamp}.mp4"
            output = self._root / "downloads" / filename
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"video")

    monkeypatch.setattr(adapter, "_ensure_client", fake_ensure_client)
    monkeypatch.setattr(adapter, "_fetch_litter_records_for_day", fake_fetch_litter_records_for_day)
    monkeypatch.setattr(
        adapter,
        "_collect_devices",
        lambda _client: [
            (
                PetKitDevice(
                    id="108228",
                    name="客厅猫厕所",
                    serial_number="serial-1",
                    household_id="house-1",
                ),
                entity,
            ),
        ],
    )
    monkeypatch.setattr(petkit_module, "DownloadDecryptMedia", FakeDownloader)

    async def run_test() -> None:
        destination = tmp_path / "video.mp4"
        result = await adapter.download_media(media, destination)

        assert result == destination
        assert destination.exists()
        assert destination.read_bytes() == b"video"
        assert adapter._media_cache[media.dedupe_key] is cloud_media
        assert downloaded_media == ["108228_1775777657"]
        assert fetched_days == ["2026-04-09"]
        assert get_devices_calls == 1

    asyncio.run(run_test())


def test_download_media_raises_clear_error_when_target_media_cannot_be_reloaded(
    monkeypatch,
    tmp_path: Path,
) -> None:
    adapter = PetKitApiAdapter(
        username="user@example.com",
        password="secret",
        region="cn",
        timezone_name="Asia/Shanghai",
    )
    media = PetKitMedia(
        id="108228_1775777657",
        device_id="108228",
        started_at=datetime(2026, 4, 9, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        cover_url="https://example.com/cover.jpg",
        encrypted_download_url="https://example.com/video.m3u8",
        source_day="2026-04-09",
    )
    other_cloud_media = MediaCloud(
        event_id="108228_1775777000",
        event_type=SimpleNamespace(value="toileting"),
        device_id=108228,
        user_id=42,
        image="https://example.com/other-cover.jpg",
        video="https://example.com/other-video.m3u8",
        filepath="108228/20260409/toileting",
        aes_key="test-key",
        timestamp=1775777000,
    )
    entity = SimpleNamespace(
        name="客厅猫厕所",
        device_nfo=SimpleNamespace(device_id="108228", device_type="t4", type_code=9),
        device_records=[],
    )

    async def fake_get_devices_data() -> None:
        return None

    async def fake_gather_all_media_from_cloud(_entities) -> list[MediaCloud]:
        return [other_cloud_media]

    client = SimpleNamespace(
        get_devices_data=fake_get_devices_data,
        media_manager=SimpleNamespace(gather_all_media_from_cloud=fake_gather_all_media_from_cloud),
    )

    async def fake_ensure_client():
        return client

    async def fake_fetch_litter_records_for_day(*, client, entity, source_day: str):
        del client, entity, source_day
        return []

    monkeypatch.setattr(adapter, "_ensure_client", fake_ensure_client)
    monkeypatch.setattr(adapter, "_fetch_litter_records_for_day", fake_fetch_litter_records_for_day)
    monkeypatch.setattr(
        adapter,
        "_collect_devices",
        lambda _client: [
            (
                PetKitDevice(
                    id="108228",
                    name="客厅猫厕所",
                    serial_number="serial-1",
                    household_id="house-1",
                ),
                entity,
            ),
        ],
    )

    async def run_test() -> None:
        destination = tmp_path / "video.mp4"
        try:
            await adapter.download_media(media, destination)
        except KeyError as exc:
            assert exc.args == (
                "PetKit media 108228:108228_1775777657 could not be reloaded from PetKit.",
            )
        else:
            raise AssertionError("Expected download_media to raise KeyError when reload misses.")

        assert adapter._media_cache["108228:108228_1775777000"] is other_cloud_media
        assert media.dedupe_key not in adapter._media_cache

    asyncio.run(run_test())


def test_download_cover_image_decrypts_preview_when_media_cache_is_reloaded(
    monkeypatch,
    tmp_path: Path,
) -> None:
    adapter = PetKitApiAdapter(
        username="user@example.com",
        password="secret",
        region="cn",
        timezone_name="Asia/Shanghai",
    )
    media = PetKitMedia(
        id="108228_1775777657",
        device_id="108228",
        started_at=datetime(2026, 4, 9, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        cover_url="https://example.com/cover.jpg",
        encrypted_download_url="https://example.com/video.m3u8",
        source_day="2026-04-09",
    )
    cloud_media = MediaCloud(
        event_id="108228_1775777657",
        event_type=SimpleNamespace(value="toileting"),
        device_id=108228,
        user_id=42,
        image="https://example.com/cover.jpg",
        video="https://example.com/video.m3u8",
        filepath="108228/20260409/toileting",
        aes_key="test-key",
        timestamp=1775777657,
    )
    entity = SimpleNamespace(
        name="客厅猫厕所",
        device_nfo=SimpleNamespace(device_id="108228", device_type="t4", type_code=9),
        device_records=[],
    )
    downloaded_media: list[tuple[str, list[str]]] = []

    async def fake_get_devices_data() -> None:
        return None

    async def fake_gather_all_media_from_cloud(_entities) -> list[MediaCloud]:
        return [cloud_media]

    client = SimpleNamespace(
        get_devices_data=fake_get_devices_data,
        media_manager=SimpleNamespace(gather_all_media_from_cloud=fake_gather_all_media_from_cloud),
    )

    async def fake_ensure_client():
        return client

    async def fake_fetch_litter_records_for_day(*, client, entity, source_day: str):
        del client, entity, source_day
        return []

    class FakeDownloader:
        def __init__(self, root: Path, client) -> None:
            self._root = root
            self._client = client

        async def download_file(self, media_cloud: MediaCloud, media_types) -> None:
            assert self._client is client
            downloaded_media.append((media_cloud.event_id, list(media_types)))
            filename = f"{media_cloud.device_id}_{media_cloud.timestamp}.jpg"
            output = self._root / "downloads" / filename
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"jpeg-data")

    monkeypatch.setattr(adapter, "_ensure_client", fake_ensure_client)
    monkeypatch.setattr(adapter, "_fetch_litter_records_for_day", fake_fetch_litter_records_for_day)
    monkeypatch.setattr(
        adapter,
        "_collect_devices",
        lambda _client: [
            (
                PetKitDevice(
                    id="108228",
                    name="客厅猫厕所",
                    serial_number="serial-1",
                    household_id="house-1",
                ),
                entity,
            ),
        ],
    )
    monkeypatch.setattr(petkit_module, "DownloadDecryptMedia", FakeDownloader)

    async def run_test() -> None:
        destination = tmp_path / "cover.jpg"
        result = await adapter.download_cover_image(media, destination)

        assert result == destination
        assert destination.read_bytes() == b"jpeg-data"
        assert downloaded_media == [("108228_1775777657", ["jpg"])]

    asyncio.run(run_test())
