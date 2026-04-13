from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class EliminationType(StrEnum):
    POOP = "poop"
    PEE = "pee"
    BOTH = "both"
    UNKNOWN = "unknown"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PetKitDevice:
    id: str
    name: str
    serial_number: str
    household_id: str


@dataclass(frozen=True, slots=True)
class PetKitMedia:
    id: str
    device_id: str
    started_at: datetime
    cover_url: str | None
    encrypted_download_url: str | None
    source_day: str
    pet_name: str | None = None

    @property
    def dedupe_key(self) -> str:
        return f"{self.device_id}:{self.id}"


@dataclass(frozen=True, slots=True)
class PipelineRequest:
    media: PetKitMedia
    pet_id: str | None = None


@dataclass(frozen=True, slots=True)
class ScreenshotArtifact:
    path: Path
    captured_at: datetime


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    event_time: datetime
    event_offset_seconds: float
    elimination_type: EliminationType
    stool_score: str | None
    stool_shape_note: str | None
    confidence: float
    raw_summary: str


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    media_id: str
    screenshot: ScreenshotArtifact
    analysis: AnalysisResult
    feishu_record_id: str | None = None


@dataclass(frozen=True, slots=True)
class MediaTask:
    id: str
    media: PetKitMedia
    status: JobStatus
    discovered_at: datetime
    updated_at: datetime
    attempts: int = 0
    last_error: str | None = None
    last_error_kind: str | None = None
    next_attempt_at: datetime | None = None
    finished_at: datetime | None = None
    feishu_record_id: str | None = None
    event_time: datetime | None = None
    elimination_type: EliminationType | None = None
    stool_score: str | None = None
    stool_shape_note: str | None = None
    confidence: float | None = None
    raw_summary: str | None = None


@dataclass(frozen=True, slots=True)
class SchedulerPollResult:
    source_day: str
    scanned_device_count: int
    discovered_media_count: int
    enqueued_task_count: int
    deduped_task_count: int
    dispatched_task_count: int = 0


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    total: int
    queued: int
    running: int
    succeeded: int
    failed: int


@dataclass(frozen=True, slots=True)
class IntegrationSnapshot:
    petkit_ready: bool
    gemini_ready: bool
    feishu_ready: bool


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    generated_at: datetime
    integration: IntegrationSnapshot
    queue: QueueSnapshot
    poll_interval_seconds: int
    refresh_interval_seconds: int
    recent_tasks: list[MediaTask]
