from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from meow_toilet.adapters.petkit import PetKitApiAdapter
from meow_toilet.config import get_settings
from meow_toilet.domain.entities import SchedulerPollResult
from meow_toilet.observability import configure_logging
from meow_toilet.runtime import create_job_dispatcher
from meow_toilet.services.artifacts import PersistentArtifactStore
from meow_toilet.services.interfaces import (
    JobDispatcher,
    MediaTaskStore,
    PetKitGateway,
    SchedulerStateStore,
)
from meow_toilet.services.scheduler_state import JsonFileSchedulerStateStore
from meow_toilet.services.sql_task_store import SqlAlchemyMediaTaskStore
import structlog


@dataclass(frozen=True, slots=True)
class SchedulerHeartbeat:
    poll_interval_seconds: int
    check_interval_seconds: int


def build_heartbeat() -> SchedulerHeartbeat:
    settings = get_settings()
    return SchedulerHeartbeat(
        poll_interval_seconds=settings.petkit_poll_interval_seconds,
        check_interval_seconds=settings.petkit_poll_check_interval_seconds,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PetKitPollingScheduler:
    def __init__(
        self,
        *,
        petkit: PetKitGateway,
        task_store: MediaTaskStore,
        artifact_store: PersistentArtifactStore | None = None,
        dispatcher: JobDispatcher | None = None,
        state_store: SchedulerStateStore | None = None,
        poll_interval_seconds: int = 21600,
        now_provider: Callable[[], datetime] = _utc_now,
        device_ids: list[str] | None = None,
    ) -> None:
        self._petkit = petkit
        self._task_store = task_store
        self._artifact_store = artifact_store
        self._dispatcher = dispatcher
        self._state_store = state_store
        self._poll_interval_seconds = poll_interval_seconds
        self._now_provider = now_provider
        self._device_ids = set(device_ids or [])
        self._logger = structlog.get_logger(__name__)

    async def get_last_successful_poll_at(self) -> datetime | None:
        if self._state_store is None:
            return None
        return await self._state_store.get_last_successful_poll_at()

    async def should_poll(self) -> bool:
        last_successful_poll_at = await self.get_last_successful_poll_at()
        if last_successful_poll_at is None:
            return True
        return self._now_provider() - last_successful_poll_at >= timedelta(
            seconds=self._poll_interval_seconds,
        )

    async def next_poll_due_at(self) -> datetime | None:
        last_successful_poll_at = await self.get_last_successful_poll_at()
        if last_successful_poll_at is None:
            return None
        return last_successful_poll_at + timedelta(seconds=self._poll_interval_seconds)

    async def poll_due(self, *, source_day: str | None = None) -> SchedulerPollResult | None:
        if not await self.should_poll():
            return None
        return await self.poll(source_day=source_day)

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
                preview_path = await self._ensure_persisted_preview(media)
                _task, created = await self._task_store.enqueue_media(
                    media,
                    discovered_at=self._now_provider(),
                    preview_path=preview_path,
                )
                if created:
                    enqueued_task_count += 1
                    if self._dispatcher is not None:
                        dispatched = await self._dispatcher.enqueue_media_task(media.dedupe_key)
                        dispatched_task_count += int(dispatched)
                else:
                    deduped_task_count += 1

        if self._state_store is not None:
            await self._state_store.set_last_successful_poll_at(self._now_provider())

        return SchedulerPollResult(
            source_day=requested_day,
            scanned_device_count=len(devices),
            discovered_media_count=discovered_media_count,
            enqueued_task_count=enqueued_task_count,
            deduped_task_count=deduped_task_count,
            dispatched_task_count=dispatched_task_count,
        )

    async def _ensure_persisted_preview(self, media) -> Path | None:
        existing_task = await self._task_store.get_task(media.dedupe_key)
        if existing_task is not None and existing_task.preview_path and existing_task.preview_path.exists():
            return existing_task.preview_path
        if self._artifact_store is None or not media.cover_url:
            return None

        try:
            with TemporaryDirectory() as temp_root:
                temp_path = Path(temp_root) / "preview.jpg"
                downloaded_path = await self._petkit.download_cover_image(media, temp_path)
                return await self._artifact_store.persist_preview(
                    task_id=media.dedupe_key,
                    source_path=downloaded_path,
                )
        except Exception as exc:
            self._logger.warning(
                "preview_persist_failed",
                task_id=media.dedupe_key,
                media_id=media.id,
                device_id=media.device_id,
                error=str(exc),
            )
            return None


async def _run_cli(args: argparse.Namespace) -> int:
    configure_logging()
    settings = get_settings()
    task_store = SqlAlchemyMediaTaskStore(settings.database_url)
    dispatcher = create_job_dispatcher(settings)
    petkit = PetKitApiAdapter.from_settings(settings)
    state_store = JsonFileSchedulerStateStore(settings.scheduler_state_path)
    logger = structlog.get_logger(__name__)
    scheduler = PetKitPollingScheduler(
        petkit=petkit,
        task_store=task_store,
        artifact_store=PersistentArtifactStore(settings.screenshot_root, settings.preview_root),
        dispatcher=dispatcher,
        state_store=state_store,
        poll_interval_seconds=settings.petkit_poll_interval_seconds,
        device_ids=settings.petkit_device_id_list,
    )
    try:
        if args.loop:
            while True:
                try:
                    result = await scheduler.poll_due(source_day=args.source_day)
                    if result is not None:
                        _print_result(result)
                    else:
                        last_successful_poll_at = await scheduler.get_last_successful_poll_at()
                        next_poll_due_at = await scheduler.next_poll_due_at()
                        logger.info(
                            "scheduler_poll_not_due",
                            last_successful_poll_at=(
                                last_successful_poll_at.isoformat()
                                if last_successful_poll_at is not None
                                else None
                            ),
                            next_poll_due_at=(
                                next_poll_due_at.isoformat() if next_poll_due_at is not None else None
                            ),
                        )
                except Exception:
                    logger.exception("scheduler_poll_iteration_failed")
                await asyncio.sleep(settings.petkit_poll_check_interval_seconds)
            raise AssertionError("Unreachable scheduler loop exit.")
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
        help="Historical day to query from PetKit, in YYYY-MM-DD format. Defaults to today when omitted.",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Wake up on the lightweight check interval and poll only when the persisted schedule is due.",
    )
    return asyncio.run(_run_cli(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
