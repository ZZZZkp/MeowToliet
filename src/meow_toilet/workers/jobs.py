from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime

from arq.connections import RedisSettings

from meow_toilet.adapters.feishu import FeishuBitableSink
from meow_toilet.adapters.gemini import GeminiAnalyzer
from meow_toilet.adapters.petkit import PetKitApiAdapter
from meow_toilet.adapters.video import FfmpegVideoProcessor
from meow_toilet.config import get_settings
from meow_toilet.domain.entities import MediaTask, PipelineRequest
from meow_toilet.runtime import create_task_store
from meow_toilet.services.interfaces import MediaTaskStore
from meow_toilet.services.pipeline import LitterEventPipeline
from meow_toilet.services.temp_files import TemporaryMediaStore


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MediaJobWorker:
    def __init__(
        self,
        *,
        task_store: MediaTaskStore,
        pipeline: LitterEventPipeline,
        now_provider: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._task_store = task_store
        self._pipeline = pipeline
        self._now_provider = now_provider

    async def process_task(self, task_id: str) -> MediaTask | None:
        task = await self._task_store.start_task(task_id, started_at=self._now_provider())
        if task is None:
            return None
        return await self._process_started_task(task)

    async def process_next_job(self) -> MediaTask | None:
        task = await self._task_store.start_next_task(started_at=self._now_provider())
        if task is None:
            return None
        return await self._process_started_task(task)

    async def _process_started_task(self, task: MediaTask) -> MediaTask:
        try:
            outcome = await self._pipeline.run(PipelineRequest(media=task.media))
        except Exception as exc:
            return await self._task_store.mark_failed(
                task.id,
                failed_at=self._now_provider(),
                error=str(exc),
            )

        return await self._task_store.mark_succeeded(
            task.id,
            completed_at=self._now_provider(),
            outcome=outcome,
        )


async def process_media_job(media_key: str) -> dict[str, str | int | None]:
    settings = get_settings()
    task_store = create_task_store(settings)
    petkit = PetKitApiAdapter.from_settings(settings)
    analyzer = GeminiAnalyzer.from_settings(settings)
    feishu = FeishuBitableSink.from_settings(settings)
    pipeline = LitterEventPipeline(
        petkit=petkit,
        video_processor=FfmpegVideoProcessor(),
        analyzer=analyzer,
        feishu=feishu,
        temp_store=TemporaryMediaStore(settings.temp_media_root),
    )
    worker = MediaJobWorker(task_store=task_store, pipeline=pipeline)
    try:
        task = await worker.process_task(media_key)
    finally:
        await petkit.aclose()
        await analyzer.aclose()
        await feishu.aclose()
        if hasattr(task_store, "dispose"):
            task_store.dispose()

    if task is None:
        return {
            "media_key": media_key,
            "status": "not_found_or_not_queued",
            "attempts": 0,
            "feishu_record_id": None,
        }

    return {
        "media_key": task.id,
        "status": task.status.value,
        "attempts": task.attempts,
        "feishu_record_id": task.feishu_record_id,
    }


async def _run_cli(args: argparse.Namespace) -> int:
    settings = get_settings()
    task_store = create_task_store(settings)
    petkit = PetKitApiAdapter.from_settings(settings)
    analyzer = GeminiAnalyzer.from_settings(settings)
    feishu = FeishuBitableSink.from_settings(settings)
    pipeline = LitterEventPipeline(
        petkit=petkit,
        video_processor=FfmpegVideoProcessor(),
        analyzer=analyzer,
        feishu=feishu,
        temp_store=TemporaryMediaStore(settings.temp_media_root),
    )
    worker = MediaJobWorker(task_store=task_store, pipeline=pipeline)

    try:
        if args.loop:
            while True:
                task = (
                    await worker.process_task(args.task_id)
                    if args.task_id
                    else await worker.process_next_job()
                )
                _print_task_payload(task, args.task_id)
                await asyncio.sleep(args.idle_sleep_seconds)
        task = (
            await worker.process_task(args.task_id)
            if args.task_id
            else await worker.process_next_job()
        )
    finally:
        await petkit.aclose()
        await analyzer.aclose()
        await feishu.aclose()
        if hasattr(task_store, "dispose"):
            task_store.dispose()

    _print_task_payload(task, args.task_id)
    return 0


def _print_task_payload(task: MediaTask | None, requested_task_id: str | None) -> None:
    payload = (
        {
            "status": "idle",
            "task_id": requested_task_id,
        }
        if task is None
        else {
            "status": task.status.value,
            "task_id": task.id,
            "attempts": task.attempts,
            "feishu_record_id": task.feishu_record_id,
            "last_error": task.last_error,
        }
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Process one queued MeowToliet media task.")
    parser.add_argument(
        "--task-id",
        help="Specific task id to process. Defaults to the next queued task when omitted.",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Keep processing and sleep between empty polls instead of running only once.",
    )
    parser.add_argument(
        "--idle-sleep-seconds",
        type=float,
        default=5.0,
        help="Sleep duration between loop iterations when --loop is enabled.",
    )
    return asyncio.run(_run_cli(parser.parse_args()))


class WorkerSettings:
    functions = [process_media_job]
    queue_name = get_settings().arq_queue_name
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)


if __name__ == "__main__":
    raise SystemExit(main())
