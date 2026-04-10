from __future__ import annotations

from pathlib import Path

from meow_toilet.config import Settings
from meow_toilet.runtime import create_task_store
from meow_toilet.services.sql_task_store import SqlAlchemyMediaTaskStore


def test_create_task_store_falls_back_to_sqlite_when_primary_database_is_unavailable(
    tmp_path: Path,
) -> None:
    settings = Settings(
        database_url="postgresql+psycopg://meow:meow@127.0.0.1:9/meow_toilet",
        database_fallback_url=f"sqlite:///{tmp_path / 'fallback.db'}",
    )

    store = create_task_store(settings)
    try:
        assert isinstance(store, SqlAlchemyMediaTaskStore)
    finally:
        if hasattr(store, "dispose"):
            store.dispose()
