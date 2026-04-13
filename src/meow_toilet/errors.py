from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ExternalServiceError(RuntimeError):
    service: str
    operation: str
    message: str
    kind: str
    retryable: bool
    status_code: int | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.__str__())

    def __str__(self) -> str:
        parts = [f"{self.service}.{self.operation}", self.kind, self.message]
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        return " | ".join(parts)
