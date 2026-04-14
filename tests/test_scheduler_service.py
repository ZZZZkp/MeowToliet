from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from meow_toilet.domain.entities import PetKitDevice, PetKitMedia
from meow_toilet.scheduler.service import PetKitPollingScheduler
from meow_toilet.services.artifacts import PersistentArtifactStore
from meow_toilet.services.scheduler_state import InMemorySchedulerStateStore
from meow_toilet.services.task_store import InMemoryMediaTaskStore


class FakePetKitGateway:
    def __init__(self, media_by_device: dict[str, list[PetKitMedia]]) -> None:
        self.media_by_device = media_by_device
        self.ensure_session_calls = 0
        self.devices = [
            PetKitDevice(
                id=device_id,
                name=f"Litter Box {device_id}",
                serial_number=f"serial-{device_id}",
                household_id="home-1",
            )
            for device_id in media_by_device
        ]

    async def ensure_session(self) -> None:
        self.ensure_session_calls += 1

    async def list_devices(self) -> list[PetKitDevice]:
        return self.devices

    async def list_media(self, device: PetKitDevice, source_day: str) -> list[PetKitMedia]:
        return self.media_by_device[device.id]

    async def download_cover_image(self, media: PetKitMedia, destination: Path) -> Path:
        del media
        destination.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
        return destination


class FakeDispatcher:
    def __init__(self) -> None:
        self.enqueued_task_ids: list[str] = []

    async def enqueue_media_task(self, task_id: str) -> bool:
        self.enqueued_task_ids.append(task_id)
        return True


def test_scheduler_poll_enqueues_media_and_dedupes_existing_tasks(tmp_path: Path) -> None:
    media = PetKitMedia(
        id="media-1",
        device_id="device-1",
        started_at=datetime(2026, 4, 9, 10, 0, tzinfo=UTC),
        cover_url="https://example.com/cover.jpg",
        encrypted_download_url="https://example.com/video.mp4",
        source_day="2026-04-09",
    )
    petkit = FakePetKitGateway({"device-1": [media]})
    task_store = InMemoryMediaTaskStore()
    artifact_store = PersistentArtifactStore(tmp_path / "screenshots", tmp_path / "previews")
    dispatcher = FakeDispatcher()
    clock = iter(
        [
            datetime(2026, 4, 9, 10, 0, tzinfo=UTC),
            datetime(2026, 4, 9, 10, 0, 1, tzinfo=UTC),
            datetime(2026, 4, 9, 10, 5, tzinfo=UTC),
            datetime(2026, 4, 9, 10, 5, 1, tzinfo=UTC),
        ],
    )
    scheduler = PetKitPollingScheduler(
        petkit=petkit,
        task_store=task_store,
        artifact_store=artifact_store,
        dispatcher=dispatcher,
        now_provider=lambda: next(clock),
    )

    async def run_test() -> None:
        first = await scheduler.poll(source_day="2026-04-09")
        second = await scheduler.poll(source_day="2026-04-09")
        tasks = await task_store.list_tasks()

        assert first.enqueued_task_count == 1
        assert first.dispatched_task_count == 1
        assert first.deduped_task_count == 0
        assert second.enqueued_task_count == 0
        assert second.deduped_task_count == 1
        assert second.dispatched_task_count == 0
        assert first.discovered_media_count == 1
        assert petkit.ensure_session_calls == 2
        assert len(tasks) == 1
        assert tasks[0].id == "device-1:media-1"
        assert tasks[0].preview_path is not None
        assert tasks[0].preview_path.exists()
        assert tasks[0].preview_path.parent.name == "previews"
        assert dispatcher.enqueued_task_ids == ["device-1:media-1"]

    asyncio.run(run_test())


def test_scheduler_poll_due_uses_persisted_last_successful_poll_time(tmp_path: Path) -> None:
    media = PetKitMedia(
        id="media-2",
        device_id="device-2",
        started_at=datetime(2026, 4, 9, 10, 0, tzinfo=UTC),
        cover_url="https://example.com/cover-2.jpg",
        encrypted_download_url="https://example.com/video-2.mp4",
        source_day="2026-04-09",
    )
    petkit = FakePetKitGateway({"device-2": [media]})
    task_store = InMemoryMediaTaskStore()
    artifact_store = PersistentArtifactStore(tmp_path / "screenshots", tmp_path / "previews")
    state_store = InMemorySchedulerStateStore(
        last_successful_poll_at=datetime(2026, 4, 9, 4, 30, tzinfo=UTC),
    )
    clock = iter(
        [
            datetime(2026, 4, 9, 10, 0, tzinfo=UTC),
            datetime(2026, 4, 9, 10, 31, tzinfo=UTC),
            datetime(2026, 4, 9, 10, 31, 1, tzinfo=UTC),
            datetime(2026, 4, 9, 10, 31, 2, tzinfo=UTC),
            datetime(2026, 4, 9, 10, 31, 3, tzinfo=UTC),
        ],
    )
    scheduler = PetKitPollingScheduler(
        petkit=petkit,
        task_store=task_store,
        artifact_store=artifact_store,
        state_store=state_store,
        poll_interval_seconds=21600,
        now_provider=lambda: next(clock),
    )

    async def run_test() -> None:
        skipped = await scheduler.poll_due(source_day="2026-04-09")
        executed = await scheduler.poll_due(source_day="2026-04-09")
        last_successful_poll_at = await state_store.get_last_successful_poll_at()

        assert skipped is None
        assert executed is not None
        assert executed.enqueued_task_count == 1
        assert petkit.ensure_session_calls == 1
        assert last_successful_poll_at == datetime(2026, 4, 9, 10, 31, 2, tzinfo=UTC)

    asyncio.run(run_test())
