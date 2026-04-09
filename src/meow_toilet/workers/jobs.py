from __future__ import annotations

from meow_toilet.config import get_settings


async def process_media_job(media_key: str) -> dict[str, str]:
    settings = get_settings()
    return {
        "media_key": media_key,
        "status": "queued",
        "temp_media_root": str(settings.temp_media_root),
    }


if __name__ == "__main__":
    print(get_settings().to_dict())
