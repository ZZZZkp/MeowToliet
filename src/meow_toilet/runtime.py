from __future__ import annotations

from arq.connections import RedisError
from sqlalchemy.exc import NoSuchModuleError, OperationalError

from meow_toilet.config import Settings, get_settings
from meow_toilet.services.dispatcher import ArqMediaJobDispatcher, NoopJobDispatcher
from meow_toilet.services.interfaces import MediaTaskStore
from meow_toilet.services.sql_task_store import SqlAlchemyMediaTaskStore
from meow_toilet.services.task_store import InMemoryMediaTaskStore


def create_task_store(settings: Settings | None = None) -> MediaTaskStore:
    resolved_settings = settings or get_settings()
    try:
        return SqlAlchemyMediaTaskStore(resolved_settings.database_url)
    except (ModuleNotFoundError, NoSuchModuleError, OperationalError, OSError):
        fallback_url = resolved_settings.database_fallback_url
        if fallback_url and fallback_url != resolved_settings.database_url:
            return SqlAlchemyMediaTaskStore(fallback_url)
        return InMemoryMediaTaskStore()


def create_job_dispatcher(
    settings: Settings | None = None,
) -> ArqMediaJobDispatcher | NoopJobDispatcher:
    resolved_settings = settings or get_settings()
    try:
        return ArqMediaJobDispatcher(
            redis_dsn=resolved_settings.redis_url,
            queue_name=resolved_settings.arq_queue_name,
        )
    except (RedisError, OSError, ValueError):
        return NoopJobDispatcher()
