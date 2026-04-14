from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile


class InMemorySchedulerStateStore:
    def __init__(self, last_successful_poll_at: datetime | None = None) -> None:
        self._last_successful_poll_at = last_successful_poll_at
        self._lock = asyncio.Lock()

    async def get_last_successful_poll_at(self) -> datetime | None:
        async with self._lock:
            return self._last_successful_poll_at

    async def set_last_successful_poll_at(self, occurred_at: datetime) -> None:
        async with self._lock:
            self._last_successful_poll_at = occurred_at


class JsonFileSchedulerStateStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()

    async def get_last_successful_poll_at(self) -> datetime | None:
        async with self._lock:
            if not self._path.exists():
                return None
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            raw_value = payload.get("last_successful_poll_at")
            if not raw_value:
                return None
            return datetime.fromisoformat(str(raw_value))

    async def set_last_successful_poll_at(self, occurred_at: datetime) -> None:
        async with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"last_successful_poll_at": occurred_at.isoformat()}
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self._path.parent,
                delete=False,
            ) as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                temp_path = Path(handle.name)
            temp_path.replace(self._path)
