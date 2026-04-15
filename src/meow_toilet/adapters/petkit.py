from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from datetime import timedelta
from http import HTTPMethod
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

import aiohttp
from pypetkitapi import DownloadDecryptMedia, Litter, LitterRecord, MediaType, PetKitClient
from pypetkitapi.const import LITTER_WITH_CAMERA
from pypetkitapi.exceptions import PetkitSessionExpiredError
from pypetkitapi.media import MediaCloud

from meow_toilet.config import Settings
from meow_toilet.domain.entities import PetKitDevice, PetKitMedia

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Phase0PetKitProbe:
    can_login: bool = False
    can_list_devices: bool = False
    can_query_historical_media: bool = False
    can_download_encrypted_video: bool = False
    device_count: int = 0
    media_count: int = 0
    sampled_device_id: str | None = None
    sampled_media_id: str | None = None
    notes: list[str] = field(default_factory=list)


class PetKitApiAdapter:
    """围绕 py-petkit-api 的轻量适配层，用于 MeowToliet 工作流。"""

    def __init__(
        self,
        *,
        username: str,
        password: str,
        region: str,
        timezone_name: str,
        allowed_device_ids: set[str] | None = None,
        session_refresh_seconds: int | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self._region = region
        self._timezone_name = timezone_name
        self._allowed_device_ids = allowed_device_ids or set()
        self._session_refresh_seconds = session_refresh_seconds
        self._session = session
        self._owns_session = session is None
        self._client: PetKitClient | None = None
        self._device_entities: dict[str, Litter] = {}
        self._media_cache: dict[str, MediaCloud] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> PetKitApiAdapter:
        return cls(
            username=settings.petkit_email,
            password=settings.petkit_password,
            region=settings.petkit_region,
            timezone_name=settings.app_timezone,
            allowed_device_ids=set(settings.petkit_device_id_list),
            session_refresh_seconds=settings.petkit_session_refresh_seconds,
        )

    async def aclose(self) -> None:
        if self._session is not None and self._owns_session and not self._session.closed:
            await self._session.close()

    async def ensure_session(self) -> None:
        async with self._lock:
            client = await self._ensure_client()
            await self._ensure_authenticated_client(client)

    async def list_devices(self) -> list[PetKitDevice]:
        async with self._lock:
            async def operation(client: PetKitClient) -> list[PetKitDevice]:
                await client.get_devices_data()
                devices = self._collect_devices(client)
                self._device_entities = {device.id: entity for device, entity in devices}
                return [device for device, _entity in devices]

            return await self._run_with_session_recovery(operation)

    async def list_media(self, device: PetKitDevice, source_day: str) -> list[PetKitMedia]:
        async with self._lock:
            async def operation(client: PetKitClient) -> list[PetKitMedia]:
                if device.id not in self._device_entities:
                    await client.get_devices_data()
                    devices = self._collect_devices(client)
                    self._device_entities = {mapped.id: entity for mapped, entity in devices}
                entity = await self._get_device_entity(device.id)
                entity.device_records = await self._fetch_litter_records_for_day(
                    client=client,
                    entity=entity,
                    source_day=source_day,
                )
                media_items = await client.media_manager.gather_all_media_from_cloud([entity])
                result: list[PetKitMedia] = []
                for media_cloud in media_items:
                    if media_cloud.video is None:
                        continue
                    mapped_media = self._map_media_cloud(
                        media_cloud,
                        source_day,
                        pet_name=self._resolve_pet_name_for_media(
                            media_cloud=media_cloud,
                            records=entity.device_records or [],
                        ),
                    )
                    self._media_cache[mapped_media.dedupe_key] = media_cloud
                    result.append(mapped_media)
                result.sort(key=lambda item: item.started_at)
                return result

            return await self._run_with_session_recovery(operation)

    async def download_media(self, media: PetKitMedia, destination: Path) -> Path:
        async with self._lock:
            async def operation(client: PetKitClient) -> Path:
                cloud_media = self._media_cache.get(media.dedupe_key)
                if cloud_media is None:
                    cloud_media = await self._refresh_cached_media(client, media)
                if cloud_media is None:
                    raise KeyError(
                        f"PetKit media {media.dedupe_key} could not be reloaded from PetKit.",
                    )
                if cloud_media.video is None:
                    raise ValueError(
                        f"PetKit media {media.dedupe_key} does not have a video URL.",
                    )

                return await self._download_cloud_media_file(
                    client=client,
                    cloud_media=cloud_media,
                    destination=destination,
                    media_type=MediaType.VIDEO,
                    suffix=".mp4",
                    missing_error=(
                        f"PetKit media download finished without a decrypted video for {media.id}."
                    ),
                )

            return await self._run_with_session_recovery(operation)

    async def download_cover_image(self, media: PetKitMedia, destination: Path) -> Path:
        async with self._lock:
            async def operation(client: PetKitClient) -> Path:
                cloud_media = self._media_cache.get(media.dedupe_key)
                if cloud_media is None:
                    cloud_media = await self._refresh_cached_media(client, media)
                if cloud_media is None:
                    raise KeyError(
                        f"PetKit media {media.dedupe_key} could not be reloaded from PetKit.",
                    )
                if cloud_media.image is None:
                    raise ValueError(
                        f"PetKit media {media.dedupe_key} does not have a cover image URL.",
                    )
                if not cloud_media.aes_key:
                    raise ValueError(
                        f"PetKit media {media.dedupe_key} does not have a cover image AES key.",
                    )

                return await self._download_cloud_media_file(
                    client=client,
                    cloud_media=cloud_media,
                    destination=destination,
                    media_type=MediaType.IMAGE,
                    suffix=".jpg",
                    missing_error=(
                        "PetKit media download finished without a decrypted cover image "
                        f"for {media.id}."
                    ),
                )

            return await self._run_with_session_recovery(operation)

    async def get_fresh_cover_url(self, media: PetKitMedia) -> str | None:
        async with self._lock:
            async def operation(client: PetKitClient) -> str | None:
                cloud_media = await self._refresh_cached_media(client, media)
                if cloud_media is None:
                    return media.cover_url
                return cloud_media.image or media.cover_url

            return await self._run_with_session_recovery(operation)

    async def _refresh_cached_media(
        self,
        client: PetKitClient,
        media: PetKitMedia,
    ) -> MediaCloud | None:
        if media.device_id not in self._device_entities:
            await client.get_devices_data()
            devices = self._collect_devices(client)
            self._device_entities = {device.id: entity for device, entity in devices}

        entity = self._device_entities.get(media.device_id)
        if entity is None:
            return None

        entity.device_records = await self._fetch_litter_records_for_day(
            client=client,
            entity=entity,
            source_day=media.source_day,
        )
        media_items = await client.media_manager.gather_all_media_from_cloud([entity])
        matched_media: MediaCloud | None = None
        for media_cloud in media_items:
            if media_cloud.video is None:
                continue
            mapped_media = self._map_media_cloud(
                media_cloud,
                media.source_day,
                pet_name=self._resolve_pet_name_for_media(
                    media_cloud=media_cloud,
                    records=entity.device_records or [],
                ),
            )
            self._media_cache[mapped_media.dedupe_key] = media_cloud
            if mapped_media.dedupe_key == media.dedupe_key:
                matched_media = media_cloud
        return matched_media

    async def _download_cloud_media_file(
        self,
        *,
        client: PetKitClient,
        cloud_media: MediaCloud,
        destination: Path,
        media_type: str,
        suffix: str,
        missing_error: str,
    ) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=destination.parent) as temp_root:
            downloader = DownloadDecryptMedia(Path(temp_root), client)
            await downloader.download_file(cloud_media, [media_type])
            downloaded = Path(temp_root).glob(
                f"**/{cloud_media.device_id}_{cloud_media.timestamp}{suffix}",
            )
            downloaded_file = next(downloaded, None)
            if downloaded_file is None:
                raise FileNotFoundError(missing_error)
            downloaded_file.replace(destination)
        return destination

    async def run_phase0_probe(
        self,
        *,
        source_day: str,
        sample_download: bool = False,
    ) -> Phase0PetKitProbe:
        notes: list[str] = []
        probe = Phase0PetKitProbe(notes=notes)

        await self.ensure_session()
        probe = self._replace_probe(probe, can_login=True)

        devices = await self.list_devices()
        probe = self._replace_probe(
            probe,
            can_list_devices=True,
            device_count=len(devices),
        )
        if not devices:
            notes.append("No camera-capable litter boxes were returned by PetKit.")
            return probe

        target_device = devices[0]
        probe = self._replace_probe(probe, sampled_device_id=target_device.id)

        media_items = await self.list_media(target_device, source_day)
        probe = self._replace_probe(
            probe,
            can_query_historical_media=True,
            media_count=len(media_items),
        )
        if not media_items:
            notes.append(
                f"Historical query succeeded for {source_day}, but no video events were returned.",
            )
            return probe

        sample_media = media_items[0]
        probe = self._replace_probe(probe, sampled_media_id=sample_media.id)
        if not sample_download:
            notes.append(
                "Skipped sample media download. "
                "Re-run with --download-sample to verify decryption.",
            )
            return probe

        with TemporaryDirectory() as temp_root:
            sample_path = Path(temp_root) / "sample.mp4"
            await self.download_media(sample_media, sample_path)
            if sample_path.exists():
                probe = self._replace_probe(probe, can_download_encrypted_video=True)
            else:
                notes.append("Sample download call completed but no decrypted file was found.")
        return probe

    async def _ensure_client(self) -> PetKitClient:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        if self._client is None:
            self._client = PetKitClient(
                username=self._username,
                password=self._password,
                region=self._region,
                timezone=self._timezone_name,
                session=self._session,
            )
        return self._client

    async def _run_with_session_recovery(
        self,
        operation: Callable[[PetKitClient], Awaitable[T]],
    ) -> T:
        client = await self._ensure_client()
        await self._ensure_authenticated_client(client)
        try:
            return await operation(client)
        except PetkitSessionExpiredError:
            await self._login(client)
            return await operation(client)

    async def _ensure_authenticated_client(self, client: PetKitClient) -> None:
        if self._session_refresh_due(client):
            await self._login(client)
            return
        await client.validate_session()

    async def _login(self, client: PetKitClient) -> None:
        await client.login()
        self._device_entities.clear()

    def _session_refresh_due(self, client: PetKitClient) -> bool:
        if self._session_refresh_seconds is None or self._session_refresh_seconds <= 0:
            return False

        created_at = self._get_session_created_at(client)
        if created_at is None:
            return False

        return self._current_time() - created_at >= timedelta(
            seconds=self._session_refresh_seconds,
        )

    def _get_session_created_at(self, client: PetKitClient) -> datetime | None:
        session_info = getattr(client, "_session", None)
        raw_created_at = getattr(session_info, "created_at", None)
        if not raw_created_at:
            return None
        return datetime.strptime(str(raw_created_at), "%Y-%m-%dT%H:%M:%S.%f%z")

    def _current_time(self) -> datetime:
        return datetime.now(UTC)

    async def _get_device_entity(self, device_id: str) -> Litter:
        entity = self._device_entities.get(device_id)
        if entity is None:
            raise KeyError(
                f"PetKit litter device {device_id} was not found in the current account.",
            )
        return entity

    async def _fetch_litter_records_for_day(
        self,
        *,
        client: PetKitClient,
        entity: Litter,
        source_day: str,
    ) -> list[LitterRecord]:
        if entity.device_nfo is None:
            raise ValueError(f"PetKit litter entity {entity.name} is missing device metadata.")

        params = self._build_historical_record_params(
            device_id=int(entity.device_nfo.device_id),
            device_type=str(entity.device_nfo.device_type),
            type_code=int(entity.device_nfo.type_code),
            source_day=source_day,
        )
        endpoint = LitterRecord.get_endpoint(str(entity.device_nfo.device_type))
        response = await client.req.request(
            method=HTTPMethod.POST,
            url=f"{entity.device_nfo.device_type}/{endpoint}",
            params=params,
            headers=await client.get_session_id(),
        )

        if isinstance(response, dict) and isinstance(response.get("list"), list):
            response = response["list"]
        if not isinstance(response, list):
            return []
        return [LitterRecord(**item) for item in response]

    def _collect_devices(self, client: PetKitClient) -> list[tuple[PetKitDevice, Litter]]:
        devices: list[tuple[PetKitDevice, Litter]] = []
        for entity in client.petkit_entities.values():
            if not isinstance(entity, Litter):
                continue
            if entity.device_nfo is None or entity.device_nfo.device_type not in LITTER_WITH_CAMERA:
                continue
            mapped = self._map_device(entity)
            if self._allowed_device_ids and mapped.id not in self._allowed_device_ids:
                continue
            devices.append((mapped, entity))
        devices.sort(key=lambda item: (item[0].name, item[0].id))
        return devices

    def _map_device(self, entity: Litter) -> PetKitDevice:
        if entity.device_nfo is None:
            raise ValueError("Cannot map a PetKit litter device without device metadata.")
        household_id = getattr(entity.device_nfo, "group_id", 0)
        return PetKitDevice(
            id=str(entity.device_nfo.device_id),
            name=entity.name or f"PetKit Litter {entity.device_nfo.device_id}",
            serial_number=entity.sn,
            household_id=str(household_id),
        )

    def _map_media_cloud(
        self,
        media_cloud: MediaCloud,
        source_day: str,
        *,
        pet_name: str | None = None,
    ) -> PetKitMedia:
        started_at = datetime.fromtimestamp(
            media_cloud.timestamp,
            tz=ZoneInfo(self._timezone_name),
        )
        return PetKitMedia(
            id=media_cloud.event_id,
            device_id=str(media_cloud.device_id),
            started_at=started_at,
            cover_url=media_cloud.image,
            encrypted_download_url=media_cloud.video,
            source_day=source_day,
            pet_name=pet_name,
        )

    @staticmethod
    def _resolve_pet_name_for_media(
        *,
        media_cloud: MediaCloud,
        records: list[LitterRecord],
    ) -> str | None:
        for record in records:
            if getattr(record, "timestamp", None) != media_cloud.timestamp:
                continue
            pet_name = getattr(record, "pet_name", None)
            if isinstance(pet_name, str):
                stripped = pet_name.strip()
                if stripped:
                    return stripped
        return None

    def _build_historical_record_params(
        self,
        *,
        device_id: int,
        device_type: str,
        type_code: int,
        source_day: str,
    ) -> dict[str, int]:
        if device_type not in LITTER_WITH_CAMERA:
            raise ValueError(
                "Historical timestamp querying only applies to camera litter devices, "
                f"got {device_type}.",
            )
        target_date = date.fromisoformat(source_day)
        target_time = datetime.combine(
            target_date,
            time(hour=12),
            tzinfo=ZoneInfo(self._timezone_name),
        )
        return {
            "timestamp": int(target_time.timestamp()),
            "deviceId": device_id,
            "type": type_code,
        }

    @staticmethod
    def _replace_probe(probe: Phase0PetKitProbe, **changes: Any) -> Phase0PetKitProbe:
        data = {
            "can_login": probe.can_login,
            "can_list_devices": probe.can_list_devices,
            "can_query_historical_media": probe.can_query_historical_media,
            "can_download_encrypted_video": probe.can_download_encrypted_video,
            "device_count": probe.device_count,
            "media_count": probe.media_count,
            "sampled_device_id": probe.sampled_device_id,
            "sampled_media_id": probe.sampled_media_id,
            "notes": probe.notes,
        }
        data.update(changes)
        return Phase0PetKitProbe(**data)
