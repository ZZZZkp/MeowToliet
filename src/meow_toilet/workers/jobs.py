from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from datetime import timedelta

from arq.connections import RedisSettings

from meow_toilet.adapters.feishu import FeishuBitableSink
from meow_toilet.adapters.gemini import GeminiAnalyzer
from meow_toilet.adapters.petkit import PetKitApiAdapter
from meow_toilet.adapters.video import FfmpegVideoProcessor
from meow_toilet.config import get_settings
from meow_toilet.domain.entities import JobStatus, MediaTask, PipelineRequest, SyncStatus
from meow_toilet.observability import configure_logging
from meow_toilet.services.interfaces import MediaTaskStore
from meow_toilet.services.artifacts import PersistentArtifactStore
from meow_toilet.services.feishu_sync import FeishuSyncService
from meow_toilet.services.pipeline import LitterEventPipeline
from meow_toilet.services.retries import RetryPolicy, classify_failure
from meow_toilet.services.sql_task_store import SqlAlchemyMediaTaskStore
from meow_toilet.services.temp_files import TemporaryMediaStore
import structlog


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MediaJobWorker:
    def __init__(
        self,
        *,
        task_store: MediaTaskStore,
        pipeline: LitterEventPipeline,
        feishu_sync_service: FeishuSyncService,
        retry_policy: RetryPolicy,
        stale_task_timeout_seconds: int,
        now_provider: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._task_store = task_store
        self._pipeline = pipeline
        self._feishu_sync_service = feishu_sync_service
        self._retry_policy = retry_policy
        self._stale_task_timeout_seconds = stale_task_timeout_seconds
        self._now_provider = now_provider
        self._logger = structlog.get_logger(__name__)

    async def process_task(self, task_id: str) -> MediaTask | None:
        await self._recover_stale_tasks()
        task = await self._task_store.get_task(task_id)
        if task is None:
            return None
        if task.status in {JobStatus.QUEUED, JobStatus.FAILED}:
            started = await self._task_store.start_task(task_id, started_at=self._now_provider())
            if started is None:
                return task
            return await self._process_started_task(started)
        if task.status == JobStatus.SUCCEEDED and task.feishu_sync_status in {
            SyncStatus.PENDING,
            SyncStatus.FAILED,
        }:
            started_sync = await self._task_store.start_feishu_sync(
                task_id,
                started_at=self._now_provider(),
            )
            if started_sync is None:
                return task
            return await self._process_feishu_sync(started_sync)
        return task

    async def process_next_job(self) -> MediaTask | None:
        await self._recover_stale_tasks()
        task = await self._task_store.start_next_task(started_at=self._now_provider())
        if task is not None:
            return await self._process_started_task(task)
        sync_task = await self._task_store.start_next_feishu_sync(started_at=self._now_provider())
        if sync_task is not None:
            return await self._process_feishu_sync(sync_task)
        return None

    async def _recover_stale_tasks(self) -> None:
        recovered_at = self._now_provider()
        stale_before = recovered_at - timedelta(seconds=self._stale_task_timeout_seconds)
        recovery = await self._task_store.recover_stale_tasks(
            stale_before=stale_before,
            recovered_at=recovered_at,
        )
        if recovery.analysis_recovered or recovery.feishu_sync_recovered:
            self._logger.warning(
                "stale_tasks_recovered",
                analysis_recovered=recovery.analysis_recovered,
                feishu_sync_recovered=recovery.feishu_sync_recovered,
                stale_task_timeout_seconds=self._stale_task_timeout_seconds,
            )

    async def _process_started_task(self, task: MediaTask) -> MediaTask:
        try:
            outcome = await self._pipeline.run(PipelineRequest(media=task.media))
        except Exception as exc:
            failed_at = self._now_provider()
            failure = classify_failure(exc)
            should_retry = self._retry_policy.should_retry(
                attempts=task.attempts,
                classification=failure,
            )
            next_attempt_at = (
                self._retry_policy.next_attempt_at(
                    attempts=task.attempts,
                    failed_at=failed_at,
                )
                if should_retry
                else None
            )
            self._logger.warning(
                "media_task_failed",
                task_id=task.id,
                media_id=task.media.id,
                attempt=task.attempts,
                error_kind=failure.kind,
                retryable=failure.retryable,
                retry_scheduled=should_retry,
                next_attempt_at=next_attempt_at.isoformat() if next_attempt_at else None,
                error=failure.message,
            )
            return await self._task_store.mark_failed(
                task.id,
                failed_at=failed_at,
                error=failure.message,
                error_kind=failure.kind,
                next_attempt_at=next_attempt_at,
            )

        self._logger.info(
            "media_task_succeeded",
            task_id=task.id,
            media_id=task.media.id,
            attempt=task.attempts,
            feishu_record_id=outcome.feishu_record_id,
        )
        return await self._task_store.mark_succeeded(
            task.id,
            completed_at=self._now_provider(),
            outcome=outcome,
        )

    async def _process_feishu_sync(self, task: MediaTask) -> MediaTask:
        try:
            feishu_record_id = await self._feishu_sync_service.sync_task(task)
        except Exception as exc:
            failed_at = self._now_provider()
            failure = classify_failure(exc)
            should_retry = self._retry_policy.should_retry(
                attempts=task.feishu_sync_attempts,
                classification=failure,
            )
            next_attempt_at = (
                self._retry_policy.next_attempt_at(
                    attempts=task.feishu_sync_attempts,
                    failed_at=failed_at,
                )
                if should_retry
                else None
            )
            self._logger.warning(
                "feishu_sync_failed",
                task_id=task.id,
                media_id=task.media.id,
                attempt=task.feishu_sync_attempts,
                error_kind=failure.kind,
                retryable=failure.retryable,
                retry_scheduled=should_retry,
                next_attempt_at=next_attempt_at.isoformat() if next_attempt_at else None,
                error=failure.message,
            )
            return await self._task_store.mark_feishu_sync_failed(
                task.id,
                failed_at=failed_at,
                error=failure.message,
                error_kind=failure.kind,
                next_attempt_at=next_attempt_at,
            )

        self._logger.info(
            "feishu_sync_succeeded",
            task_id=task.id,
            media_id=task.media.id,
            attempt=task.feishu_sync_attempts,
            feishu_record_id=feishu_record_id,
        )
        return await self._task_store.mark_feishu_sync_succeeded(
            task.id,
            synced_at=self._now_provider(),
            feishu_record_id=feishu_record_id,
        )


async def process_media_job(media_key: str) -> dict[str, str | int | None]:
    configure_logging()
    settings = get_settings()
    task_store = SqlAlchemyMediaTaskStore(settings.database_url)
    petkit = PetKitApiAdapter.from_settings(settings)
    analyzer = GeminiAnalyzer.from_settings(settings)
    feishu = FeishuBitableSink.from_settings(settings)
    pipeline = LitterEventPipeline(
        petkit=petkit,
        video_processor=FfmpegVideoProcessor(),
        analyzer=analyzer,
        temp_store=TemporaryMediaStore(settings.temp_media_root),
        artifact_store=PersistentArtifactStore(settings.screenshot_root, settings.preview_root),
    )
    feishu_sync_service = FeishuSyncService(feishu=feishu)
    retry_policy = RetryPolicy(
        max_attempts=settings.worker_retry_max_attempts,
        backoff_seconds=settings.worker_retry_backoff_seconds,
        max_backoff_seconds=settings.worker_retry_max_backoff_seconds,
    )
    worker = MediaJobWorker(
        task_store=task_store,
        pipeline=pipeline,
        feishu_sync_service=feishu_sync_service,
        retry_policy=retry_policy,
        stale_task_timeout_seconds=settings.worker_stale_task_timeout_seconds,
    )
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
    configure_logging()
    settings = get_settings()
    task_store = SqlAlchemyMediaTaskStore(settings.database_url)
    petkit = PetKitApiAdapter.from_settings(settings)
    analyzer = GeminiAnalyzer.from_settings(settings)
    feishu = FeishuBitableSink.from_settings(settings)
    pipeline = LitterEventPipeline(
        petkit=petkit,
        video_processor=FfmpegVideoProcessor(),
        analyzer=analyzer,
        temp_store=TemporaryMediaStore(settings.temp_media_root),
        artifact_store=PersistentArtifactStore(settings.screenshot_root, settings.preview_root),
    )
    feishu_sync_service = FeishuSyncService(feishu=feishu)
    retry_policy = RetryPolicy(
        max_attempts=settings.worker_retry_max_attempts,
        backoff_seconds=settings.worker_retry_backoff_seconds,
        max_backoff_seconds=settings.worker_retry_max_backoff_seconds,
    )
    worker = MediaJobWorker(
        task_store=task_store,
        pipeline=pipeline,
        feishu_sync_service=feishu_sync_service,
        retry_policy=retry_policy,
        stale_task_timeout_seconds=settings.worker_stale_task_timeout_seconds,
    )
    logger = structlog.get_logger(__name__)

    try:
        if args.loop:
            while True:
                try:
                    task = (
                        await worker.process_task(args.task_id)
                        if args.task_id
                        else await worker.process_next_job()
                    )
                    _print_task_payload(task, args.task_id)
                except Exception:
                    logger.exception("worker_loop_iteration_failed")
                await asyncio.sleep(args.idle_sleep_seconds)
            raise AssertionError("Unreachable worker loop exit.")
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
        default=float(get_settings().worker_idle_sleep_seconds),
        help="Sleep duration between loop iterations when --loop is enabled.",
    )
    return asyncio.run(_run_cli(parser.parse_args()))


class WorkerSettings:
    functions = [process_media_job]
    queue_name = get_settings().arq_queue_name
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)


if __name__ == "__main__":
    raise SystemExit(main())
