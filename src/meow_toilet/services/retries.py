from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from meow_toilet.errors import ExternalServiceError


@dataclass(frozen=True, slots=True)
class FailureClassification:
    kind: str
    message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    backoff_seconds: int
    max_backoff_seconds: int

    def should_retry(self, *, attempts: int, classification: FailureClassification) -> bool:
        return classification.retryable and attempts < self.max_attempts

    def next_attempt_at(self, *, attempts: int, failed_at: datetime) -> datetime:
        delay_seconds = min(
            self.max_backoff_seconds,
            self.backoff_seconds * (2 ** max(0, attempts - 1)),
        )
        return failed_at + timedelta(seconds=delay_seconds)


def classify_failure(exc: Exception) -> FailureClassification:
    if isinstance(exc, ExternalServiceError):
        return FailureClassification(
            kind=exc.kind,
            message=str(exc),
            retryable=exc.retryable,
        )
    return FailureClassification(
        kind=exc.__class__.__name__,
        message=str(exc),
        retryable=False,
    )
