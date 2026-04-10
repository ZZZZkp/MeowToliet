from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime

from meow_toilet.domain.entities import JobStatus, MediaTask, PetKitMedia, PipelineOutcome


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
                ),
                key=lambda task: (task.discovered_at, task.id),
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

    def _start_task(self, task: MediaTask, started_at: datetime) -> MediaTask:
        updated = replace(
            task,
            status=JobStatus.RUNNING,
            updated_at=started_at,
            attempts=task.attempts + 1,
            last_error=None,
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
                feishu_record_id=outcome.feishu_record_id,
                event_time=outcome.analysis.event_time,
                elimination_type=outcome.analysis.elimination_type,
                stool_score=outcome.analysis.stool_score,
                stool_shape_note=outcome.analysis.stool_shape_note,
                confidence=outcome.analysis.confidence,
                raw_summary=outcome.analysis.raw_summary,
                last_error=None,
            )
            self._tasks[task_id] = updated
            return updated

    async def mark_failed(self, task_id: str, failed_at: datetime, error: str) -> MediaTask:
        async with self._lock:
            task = self._tasks[task_id]
            updated = replace(
                task,
                status=JobStatus.FAILED,
                updated_at=failed_at,
                finished_at=failed_at,
                last_error=error,
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
