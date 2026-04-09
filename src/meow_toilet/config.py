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
    redis_url: str = field(
        default_factory=lambda: _read_str("REDIS_URL", "redis://localhost:6379/0"),
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
    temp_media_root: Path = field(
        default_factory=lambda: Path(_read_str("TEMP_MEDIA_ROOT", "./tmp/media")),
    )
    max_concurrent_decodes: int = field(
        default_factory=lambda: _read_int("MAX_CONCURRENT_DECODES", 2),
    )
    max_concurrent_analyses: int = field(
        default_factory=lambda: _read_int("MAX_CONCURRENT_ANALYSES", 2),
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
            "redis_url": self.redis_url,
            "petkit_region": self.petkit_region,
            "petkit_poll_interval_seconds": self.petkit_poll_interval_seconds,
            "petkit_session_refresh_seconds": self.petkit_session_refresh_seconds,
            "petkit_device_ids": self.petkit_device_ids,
            "gemini_model": self.gemini_model,
            "temp_media_root": str(self.temp_media_root),
            "max_concurrent_decodes": self.max_concurrent_decodes,
            "max_concurrent_analyses": self.max_concurrent_analyses,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
