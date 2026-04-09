from __future__ import annotations

from dataclasses import dataclass

from meow_toilet.config import get_settings


@dataclass(frozen=True, slots=True)
class SchedulerHeartbeat:
    poll_interval_seconds: int
    refresh_interval_seconds: int


def build_heartbeat() -> SchedulerHeartbeat:
    settings = get_settings()
    return SchedulerHeartbeat(
        poll_interval_seconds=settings.petkit_poll_interval_seconds,
        refresh_interval_seconds=settings.petkit_session_refresh_seconds,
    )


if __name__ == "__main__":
    heartbeat = build_heartbeat()
    print(
        f"poll={heartbeat.poll_interval_seconds}s refresh={heartbeat.refresh_interval_seconds}s",
    )

