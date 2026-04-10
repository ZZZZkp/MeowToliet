from __future__ import annotations

from arq import create_pool
from arq.connections import RedisSettings
from redis.exceptions import RedisError


class NoopJobDispatcher:
    async def enqueue_media_task(self, task_id: str) -> bool:
        return False


class ArqMediaJobDispatcher:
    def __init__(
        self,
        *,
        redis_dsn: str,
        queue_name: str,
        job_name: str = "process_media_job",
    ) -> None:
        self._redis_settings = RedisSettings.from_dsn(redis_dsn)
        self._queue_name = queue_name
        self._job_name = job_name

    async def enqueue_media_task(self, task_id: str) -> bool:
        try:
            redis = await create_pool(
                self._redis_settings,
                default_queue_name=self._queue_name,
            )
        except (OSError, RedisError):
            return False
        try:
            job = await redis.enqueue_job(
                self._job_name,
                task_id,
                _queue_name=self._queue_name,
            )
        except (OSError, RedisError):
            return False
        finally:
            await redis.aclose()
        return job is not None
