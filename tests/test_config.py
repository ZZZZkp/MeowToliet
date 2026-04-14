from __future__ import annotations

from pathlib import Path

from meow_toilet.config import Settings


def test_settings_detect_configured_integrations() -> None:
    settings = Settings(
        petkit_email="user@example.com",
        petkit_password="secret",
        gemini_api_key="gem-key",
        feishu_app_id="app-id",
        feishu_app_secret="app-secret",
        feishu_bitable_app_token="bitable-token",
        feishu_bitable_table_id="table-id",
        temp_media_root=Path("/tmp/meow"),
    )

    assert settings.petkit_credentials_configured is True
    assert settings.gemini_configured is True
    assert settings.feishu_configured is True
    assert settings.temp_media_root == Path("/tmp/meow")


def test_settings_default_worker_stale_timeout_seconds() -> None:
    settings = Settings()

    assert settings.worker_stale_task_timeout_seconds == 600
