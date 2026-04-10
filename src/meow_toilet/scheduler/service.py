from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from meow_toilet.adapters.petkit import PetKitApiAdapter
from meow_toilet.config import get_settings
from meow_toilet.domain.entities import SchedulerPollResult
from meow_toilet.runtime import create_job_dispatcher, create_task_store
from meow_toilet.services.interfaces import JobDispatcher, MediaTaskStore, PetKitGateway


@dataclass(frozen=True, slots=True)
class SchedulerHeartbeat:
    poll_interval_seconds: int
    refresh_interval_seconds: int


def build_heartbeat() -> SchedulerHeartbeat:
    settings = get_settings()
    return SchedulerHeartbeat(
        poll_interval_seconds=settings.petkit_poll_interval_seconds,
        refresh_interval_seconds=settings.petkit_session_refresh_seconds,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PetKitPollingScheduler:
    def __init__(
        self,
        *,
        petkit: PetKitGateway,
        task_store: MediaTaskStore,
        dispatcher: JobDispatcher | None = None,
        now_provider: Callable[[], datetime] = _utc_now,
        device_ids: list[str] | None = None,
    ) -> None:
        self._petkit = petkit
        self._task_store = task_store
        self._dispatcher = dispatcher
        self._now_provider = now_provider
        self._device_ids = set(device_ids or [])

    async def poll(self, *, source_day: str | None = None) -> SchedulerPollResult:
        requested_day = source_day or self._now_provider().date().isoformat()
        await self._petkit.ensure_session()
        devices = await self._petkit.list_devices()
        if self._device_ids:
            devices = [device for device in devices if device.id in self._device_ids]

        discovered_media_count = 0
        enqueued_task_count = 0
        deduped_task_count = 0
        dispatched_task_count = 0

        for device in devices:
            media_items = await self._petkit.list_media(device, requested_day)
            discovered_media_count += len(media_items)
            for media in media_items:
                _task, created = await self._task_store.enqueue_media(
                    media,
                    discovered_at=self._now_provider(),
                )
                if created:
                    enqueued_task_count += 1
                    if self._dispatcher is not None:
                        dispatched = await self._dispatcher.enqueue_media_task(media.dedupe_key)
                        dispatched_task_count += int(dispatched)
                else:
                    deduped_task_count += 1

        return SchedulerPollResult(
            source_day=requested_day,
            scanned_device_count=len(devices),
            discovered_media_count=discovered_media_count,
            enqueued_task_count=enqueued_task_count,
            deduped_task_count=deduped_task_count,
            dispatched_task_count=dispatched_task_count,
        )


async def _run_cli(args: argparse.Namespace) -> int:
    settings = get_settings()
    task_store = create_task_store(settings)
    dispatcher = create_job_dispatcher(settings)
    petkit = PetKitApiAdapter.from_settings(settings)
    scheduler = PetKitPollingScheduler(
        petkit=petkit,
        task_store=task_store,
        dispatcher=dispatcher,
        device_ids=settings.petkit_device_id_list,
    )
    try:
        if args.loop:
            while True:
                result = await scheduler.poll(source_day=args.source_day)
                _print_result(result)
                await asyncio.sleep(settings.petkit_poll_interval_seconds)
        else:
            result = await scheduler.poll(source_day=args.source_day)
    finally:
        await petkit.aclose()
        if hasattr(task_store, "dispose"):
            task_store.dispose()

    _print_result(result)
    return 0


def _print_result(result: SchedulerPollResult) -> None:
    print(
        json.dumps(
            {
                "source_day": result.source_day,
                "scanned_device_count": result.scanned_device_count,
                "discovered_media_count": result.discovered_media_count,
                "enqueued_task_count": result.enqueued_task_count,
                "deduped_task_count": result.deduped_task_count,
                "dispatched_task_count": result.dispatched_task_count,
            },
            ensure_ascii=False,
            indent=2,
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll PetKit media and enqueue MeowToliet tasks.")
    parser.add_argument(
        "--source-day",
        default=datetime.now().date().isoformat(),
        help="Historical day to query from PetKit, in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Keep polling on the configured interval instead of running only once.",
    )
    return asyncio.run(_run_cli(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
