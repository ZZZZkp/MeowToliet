from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _load_dotenv() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _read_bool(name: str, default: bool) -> bool:
    _load_dotenv()
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _read_int(name: str, default: int) -> int:
    _load_dotenv()
    value = os.getenv(name)
    if value is None:
        return default
    return int(value)


def _read_str(name: str, default: str) -> str:
    _load_dotenv()
    return os.getenv(name, default)


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str = field(default_factory=lambda: _read_str("APP_ENV", "development"))
    app_debug: bool = field(default_factory=lambda: _read_bool("APP_DEBUG", True))
    app_host: str = field(default_factory=lambda: _read_str("APP_HOST", "0.0.0.0"))
    app_port: int = field(default_factory=lambda: _read_int("APP_PORT", 8000))
    app_timezone: str = field(
        default_factory=lambda: _read_str("APP_TIMEZONE", "Asia/Shanghai"),
    )
    database_url: str = field(
        default_factory=lambda: _read_str(
            "DATABASE_URL",
            "postgresql+psycopg://meow:meow@localhost:5432/meow_toilet",
        ),
    )
    database_fallback_url: str = field(
        default_factory=lambda: _read_str(
            "DATABASE_FALLBACK_URL",
            "sqlite:///./tmp/meow_toilet.db",
        ),
    )
    redis_url: str = field(
        default_factory=lambda: _read_str("REDIS_URL", "redis://localhost:6379/0"),
    )
    arq_queue_name: str = field(
        default_factory=lambda: _read_str("ARQ_QUEUE_NAME", "meow_toilet:media"),
    )
    petkit_email: str = field(default_factory=lambda: _read_str("PETKIT_EMAIL", ""))
    petkit_password: str = field(
        default_factory=lambda: _read_str("PETKIT_PASSWORD", ""),
    )
    petkit_region: str = field(default_factory=lambda: _read_str("PETKIT_REGION", "cn"))
    petkit_poll_interval_seconds: int = field(
        default_factory=lambda: _read_int("PETKIT_POLL_INTERVAL_SECONDS", 300),
    )
    petkit_session_refresh_seconds: int = field(
        default_factory=lambda: _read_int("PETKIT_SESSION_REFRESH_SECONDS", 1800),
    )
    petkit_device_ids: str = field(default_factory=lambda: _read_str("PETKIT_DEVICE_IDS", ""))
    gemini_api_key: str = field(default_factory=lambda: _read_str("GEMINI_API_KEY", ""))
    gemini_model: str = field(
        default_factory=lambda: _read_str("GEMINI_MODEL", "gemini-2.5-flash"),
    )
    feishu_app_id: str = field(default_factory=lambda: _read_str("FEISHU_APP_ID", ""))
    feishu_app_secret: str = field(
        default_factory=lambda: _read_str("FEISHU_APP_SECRET", ""),
    )
    feishu_bitable_app_token: str = field(
        default_factory=lambda: _read_str("FEISHU_BITABLE_APP_TOKEN", ""),
    )
    feishu_bitable_table_id: str = field(
        default_factory=lambda: _read_str("FEISHU_BITABLE_TABLE_ID", ""),
    )
    feishu_field_media_id: str = field(
        default_factory=lambda: _read_str("FEISHU_FIELD_MEDIA_ID", "Media ID"),
    )
    feishu_field_device_id: str = field(
        default_factory=lambda: _read_str("FEISHU_FIELD_DEVICE_ID", "Device ID"),
    )
    feishu_field_event_time: str = field(
        default_factory=lambda: _read_str("FEISHU_FIELD_EVENT_TIME", "Event Time"),
    )
    feishu_field_pet_name: str = field(
        default_factory=lambda: _read_str("FEISHU_FIELD_PET_NAME", "Pet Name"),
    )
    feishu_field_elimination_type: str = field(
        default_factory=lambda: _read_str("FEISHU_FIELD_ELIMINATION_TYPE", "Elimination Type"),
    )
    feishu_field_stool_score: str = field(
        default_factory=lambda: _read_str("FEISHU_FIELD_STOOL_SCORE", "Stool Score"),
    )
    feishu_field_stool_shape_note: str = field(
        default_factory=lambda: _read_str("FEISHU_FIELD_STOOL_SHAPE_NOTE", "Stool Shape Note"),
    )
    feishu_field_confidence: str = field(
        default_factory=lambda: _read_str("FEISHU_FIELD_CONFIDENCE", "Confidence"),
    )
    feishu_field_raw_summary: str = field(
        default_factory=lambda: _read_str("FEISHU_FIELD_RAW_SUMMARY", "Raw Summary"),
    )
    feishu_field_screenshot: str = field(
        default_factory=lambda: _read_str("FEISHU_FIELD_SCREENSHOT", "Screenshot"),
    )
    feishu_field_source_day: str = field(
        default_factory=lambda: _read_str("FEISHU_FIELD_SOURCE_DAY", "Source Day"),
    )
    temp_media_root: Path = field(
        default_factory=lambda: Path(_read_str("TEMP_MEDIA_ROOT", "./tmp/media")),
    )
    screenshot_root: Path = field(
        default_factory=lambda: Path(_read_str("SCREENSHOT_ROOT", "./tmp/screenshots")),
    )
    max_concurrent_decodes: int = field(
        default_factory=lambda: _read_int("MAX_CONCURRENT_DECODES", 2),
    )
    max_concurrent_analyses: int = field(
        default_factory=lambda: _read_int("MAX_CONCURRENT_ANALYSES", 2),
    )
    worker_retry_max_attempts: int = field(
        default_factory=lambda: _read_int("WORKER_RETRY_MAX_ATTEMPTS", 5),
    )
    worker_retry_backoff_seconds: int = field(
        default_factory=lambda: _read_int("WORKER_RETRY_BACKOFF_SECONDS", 30),
    )
    worker_retry_max_backoff_seconds: int = field(
        default_factory=lambda: _read_int("WORKER_RETRY_MAX_BACKOFF_SECONDS", 900),
    )
    worker_idle_sleep_seconds: int = field(
        default_factory=lambda: _read_int("WORKER_IDLE_SLEEP_SECONDS", 5),
    )

    @property
    def petkit_credentials_configured(self) -> bool:
        return bool(self.petkit_email and self.petkit_password)

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def petkit_device_id_list(self) -> list[str]:
        return [part.strip() for part in self.petkit_device_ids.split(",") if part.strip()]

    @property
    def feishu_configured(self) -> bool:
        return bool(
            self.feishu_app_id
            and self.feishu_app_secret
            and self.feishu_bitable_app_token
            and self.feishu_bitable_table_id
        )

    def to_dict(self) -> dict[str, str | bool | int]:
        return {
            "app_env": self.app_env,
            "app_debug": self.app_debug,
            "app_host": self.app_host,
            "app_port": self.app_port,
            "app_timezone": self.app_timezone,
            "database_url": self.database_url,
            "database_fallback_url": self.database_fallback_url,
            "redis_url": self.redis_url,
            "arq_queue_name": self.arq_queue_name,
            "petkit_region": self.petkit_region,
            "petkit_poll_interval_seconds": self.petkit_poll_interval_seconds,
            "petkit_session_refresh_seconds": self.petkit_session_refresh_seconds,
            "petkit_device_ids": self.petkit_device_ids,
            "gemini_model": self.gemini_model,
            "feishu_bitable_app_token": self.feishu_bitable_app_token,
            "feishu_bitable_table_id": self.feishu_bitable_table_id,
            "temp_media_root": str(self.temp_media_root),
            "screenshot_root": str(self.screenshot_root),
            "max_concurrent_decodes": self.max_concurrent_decodes,
            "max_concurrent_analyses": self.max_concurrent_analyses,
            "worker_retry_max_attempts": self.worker_retry_max_attempts,
            "worker_retry_backoff_seconds": self.worker_retry_backoff_seconds,
            "worker_retry_max_backoff_seconds": self.worker_retry_max_backoff_seconds,
            "worker_idle_sleep_seconds": self.worker_idle_sleep_seconds,
        }

    @property
    def feishu_field_mapping(self) -> dict[str, str]:
        return {
            "media_id": self.feishu_field_media_id,
            "device_id": self.feishu_field_device_id,
            "event_time": self.feishu_field_event_time,
            "pet_name": self.feishu_field_pet_name,
            "elimination_type": self.feishu_field_elimination_type,
            "stool_score": self.feishu_field_stool_score,
            "stool_shape_note": self.feishu_field_stool_shape_note,
            "confidence": self.feishu_field_confidence,
            "raw_summary": self.feishu_field_raw_summary,
            "screenshot": self.feishu_field_screenshot,
            "source_day": self.feishu_field_source_day,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
