from __future__ import annotations

from functools import lru_cache

from meow_toilet.runtime import create_job_dispatcher, create_task_store
from meow_toilet.services.dashboard import DashboardMediaService, DashboardSnapshotService
from meow_toilet.services.interfaces import JobDispatcher, MediaTaskStore
from meow_toilet.services.operations import ManualOperationsService


@lru_cache(maxsize=1)
def get_task_store() -> MediaTaskStore:
    return create_task_store()


@lru_cache(maxsize=1)
def get_job_dispatcher() -> JobDispatcher:
    return create_job_dispatcher()


def get_dashboard_snapshot_service() -> DashboardSnapshotService:
    return DashboardSnapshotService(task_store=get_task_store())


def get_dashboard_media_service() -> DashboardMediaService:
    return DashboardMediaService(task_store=get_task_store())


def get_manual_operations_service() -> ManualOperationsService:
    return ManualOperationsService(
        task_store=get_task_store(),
        dispatcher=get_job_dispatcher(),
    )
