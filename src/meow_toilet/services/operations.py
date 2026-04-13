from __future__ import annotations

from collections.abc import Callable

from meow_toilet.adapters.feishu import FeishuBitableSink
from meow_toilet.adapters.gemini import GeminiAnalyzer
from meow_toilet.adapters.petkit import PetKitApiAdapter
from meow_toilet.adapters.video import FfmpegVideoProcessor
from meow_toilet.config import Settings, get_settings
from meow_toilet.domain.entities import MediaTask, SchedulerPollResult
from meow_toilet.scheduler.service import PetKitPollingScheduler
from meow_toilet.services.artifacts import PersistentArtifactStore
from meow_toilet.services.feishu_sync import FeishuSyncService
from meow_toilet.services.interfaces import JobDispatcher, MediaTaskStore
from meow_toilet.services.pipeline import LitterEventPipeline
from meow_toilet.services.retries import RetryPolicy
from meow_toilet.services.temp_files import TemporaryMediaStore
from meow_toilet.workers.jobs import MediaJobWorker


class ManualOperationsService:
    def __init__(
        self,
        *,
        task_store: MediaTaskStore,
        dispatcher: JobDispatcher,
        settings_provider: Callable[[], Settings] = get_settings,
    ) -> None:
        self._task_store = task_store
        self._dispatcher = dispatcher
        self._settings_provider = settings_provider

    async def poll_once(self, *, source_day: str | None = None) -> SchedulerPollResult:
        settings = self._settings_provider()
        petkit = PetKitApiAdapter.from_settings(settings)
        scheduler = PetKitPollingScheduler(
            petkit=petkit,
            task_store=self._task_store,
            artifact_store=PersistentArtifactStore(settings.screenshot_root, settings.preview_root),
            dispatcher=self._dispatcher,
            device_ids=settings.petkit_device_id_list,
        )
        try:
            return await scheduler.poll(source_day=source_day)
        finally:
            await petkit.aclose()

    async def process_next_task(self) -> MediaTask | None:
        worker, closers = self._build_worker()
        try:
            return await worker.process_next_job()
        finally:
            await self._close_resources(*closers)

    async def process_task(self, task_id: str) -> MediaTask | None:
        worker, closers = self._build_worker()
        try:
            return await worker.process_task(task_id)
        finally:
            await self._close_resources(*closers)

    def _build_worker(
        self,
    ) -> tuple[MediaJobWorker, tuple[PetKitApiAdapter, GeminiAnalyzer, FeishuBitableSink]]:
        settings = self._settings_provider()
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
        return MediaJobWorker(
            task_store=self._task_store,
            pipeline=pipeline,
            feishu_sync_service=feishu_sync_service,
            retry_policy=retry_policy,
            stale_task_timeout_seconds=settings.worker_stale_task_timeout_seconds,
        ), (
            petkit,
            analyzer,
            feishu,
        )

    @staticmethod
    async def _close_resources(
        petkit: PetKitApiAdapter,
        analyzer: GeminiAnalyzer,
        feishu: FeishuBitableSink,
    ) -> None:
        await petkit.aclose()
        await analyzer.aclose()
        await feishu.aclose()
