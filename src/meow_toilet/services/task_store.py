from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from meow_toilet.domain.entities import JobStatus, MediaTask, PetKitMedia, PipelineOutcome, SyncStatus


class InMemoryMediaTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, MediaTask] = {}
        self._lock = asyncio.Lock()

    async def enqueue_media(
        self,
        media: PetKitMedia,
        discovered_at: datetime,
    ) -> tuple[MediaTask, bool]:
        async with self._lock:
            existing = self._tasks.get(media.dedupe_key)
            if existing is not None:
                return existing, False

            task = MediaTask(
                id=media.dedupe_key,
                media=media,
                status=JobStatus.QUEUED,
                discovered_at=discovered_at,
                updated_at=discovered_at,
                next_attempt_at=discovered_at,
            )
            self._tasks[task.id] = task
            return task, True

    async def start_next_task(self, started_at: datetime) -> MediaTask | None:
        async with self._lock:
            queued_tasks = sorted(
                (
                    task
                    for task in self._tasks.values()
                    if task.status == JobStatus.QUEUED
                    and (task.next_attempt_at is None or task.next_attempt_at <= started_at)
                ),
                key=lambda task: (task.next_attempt_at or task.discovered_at, task.discovered_at, task.id),
            )
            if not queued_tasks:
                return None

            task = queued_tasks[0]
            return self._start_task(task, started_at)

    async def start_task(self, task_id: str, started_at: datetime) -> MediaTask | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status not in {JobStatus.QUEUED, JobStatus.FAILED}:
                return None
            return self._start_task(task, started_at)

    async def start_feishu_sync(self, task_id: str, started_at: datetime) -> MediaTask | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status != JobStatus.SUCCEEDED:
                return None
            if task.feishu_sync_status not in {SyncStatus.PENDING, SyncStatus.FAILED}:
                return None
            return self._start_feishu_sync(task, started_at)

    async def start_next_feishu_sync(self, started_at: datetime) -> MediaTask | None:
        async with self._lock:
            syncable_tasks = sorted(
                (
                    task
                    for task in self._tasks.values()
                    if task.status == JobStatus.SUCCEEDED
                    and task.feishu_sync_status == SyncStatus.PENDING
                    and (
                        task.feishu_sync_next_attempt_at is None
                        or task.feishu_sync_next_attempt_at <= started_at
                    )
                ),
                key=lambda task: (
                    task.feishu_sync_next_attempt_at or task.updated_at,
                    task.updated_at,
                    task.id,
                ),
            )
            if not syncable_tasks:
                return None
            return self._start_feishu_sync(syncable_tasks[0], started_at)

    def _start_task(self, task: MediaTask, started_at: datetime) -> MediaTask:
        updated = replace(
            task,
            status=JobStatus.RUNNING,
            updated_at=started_at,
            attempts=task.attempts + 1,
            last_error=None,
            last_error_kind=None,
            next_attempt_at=None,
        )
        self._tasks[task.id] = updated
        return updated

    async def mark_succeeded(
        self,
        task_id: str,
        completed_at: datetime,
        outcome: PipelineOutcome,
    ) -> MediaTask:
        async with self._lock:
            task = self._tasks[task_id]
            updated = replace(
                task,
                status=JobStatus.SUCCEEDED,
                updated_at=completed_at,
                finished_at=completed_at,
                screenshot_path=outcome.screenshot.path,
                feishu_record_id=outcome.feishu_record_id,
                feishu_sync_status=SyncStatus.PENDING,
                feishu_sync_next_attempt_at=completed_at,
                feishu_sync_last_error=None,
                feishu_sync_last_error_kind=None,
                event_time=outcome.analysis.event_time,
                elimination_type=outcome.analysis.elimination_type,
                stool_score=outcome.analysis.stool_score,
                stool_shape_note=outcome.analysis.stool_shape_note,
                confidence=outcome.analysis.confidence,
                raw_summary=outcome.analysis.raw_summary,
                last_error=None,
                last_error_kind=None,
                next_attempt_at=None,
            )
            self._tasks[task_id] = updated
            return updated

    def _start_feishu_sync(self, task: MediaTask, started_at: datetime) -> MediaTask:
        updated = replace(
            task,
            updated_at=started_at,
            feishu_sync_status=SyncStatus.RUNNING,
            feishu_sync_attempts=task.feishu_sync_attempts + 1,
            feishu_sync_last_error=None,
            feishu_sync_last_error_kind=None,
            feishu_sync_next_attempt_at=None,
        )
        self._tasks[task.id] = updated
        return updated

    async def mark_failed(
        self,
        task_id: str,
        failed_at: datetime,
        error: str,
        *,
        error_kind: str | None = None,
        next_attempt_at: datetime | None = None,
    ) -> MediaTask:
        async with self._lock:
            task = self._tasks[task_id]
            updated = replace(
                task,
                status=JobStatus.QUEUED if next_attempt_at is not None else JobStatus.FAILED,
                updated_at=failed_at,
                finished_at=failed_at,
                last_error=error,
                last_error_kind=error_kind,
                next_attempt_at=next_attempt_at,
            )
            self._tasks[task_id] = updated
            return updated

    async def mark_feishu_sync_succeeded(
        self,
        task_id: str,
        synced_at: datetime,
        *,
        feishu_record_id: str | None,
    ) -> MediaTask:
        async with self._lock:
            task = self._tasks[task_id]
            updated = replace(
                task,
                updated_at=synced_at,
                feishu_record_id=feishu_record_id or task.feishu_record_id,
                feishu_sync_status=SyncStatus.SUCCEEDED,
                feishu_sync_last_error=None,
                feishu_sync_last_error_kind=None,
                feishu_sync_next_attempt_at=None,
                feishu_synced_at=synced_at,
            )
            self._tasks[task_id] = updated
            return updated

    async def mark_feishu_sync_failed(
        self,
        task_id: str,
        failed_at: datetime,
        error: str,
        *,
        error_kind: str | None = None,
        next_attempt_at: datetime | None = None,
    ) -> MediaTask:
        async with self._lock:
            task = self._tasks[task_id]
            updated = replace(
                task,
                updated_at=failed_at,
                feishu_sync_status=(
                    SyncStatus.PENDING if next_attempt_at is not None else SyncStatus.FAILED
                ),
                feishu_sync_last_error=error,
                feishu_sync_last_error_kind=error_kind,
                feishu_sync_next_attempt_at=next_attempt_at,
            )
            self._tasks[task_id] = updated
            return updated

    async def get_task(self, task_id: str) -> MediaTask | None:
        async with self._lock:
            return self._tasks.get(task_id)

    async def list_tasks(self, *, limit: int | None = None) -> list[MediaTask]:
        async with self._lock:
            tasks = sorted(
                self._tasks.values(),
                key=lambda task: (task.updated_at, task.id),
                reverse=True,
            )
            if limit is None:
                return tasks
            return tasks[:limit]
