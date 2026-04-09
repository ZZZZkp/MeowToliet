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
