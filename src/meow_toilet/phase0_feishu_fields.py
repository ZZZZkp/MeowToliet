from __future__ import annotations

import asyncio
import json

from meow_toilet.adapters.feishu import FeishuBitableSink
from meow_toilet.config import get_settings


async def _run() -> int:
    settings = get_settings()
    if not settings.feishu_configured:
        print("Missing Feishu credentials. Fill FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_BITABLE_APP_TOKEN, and FEISHU_BITABLE_TABLE_ID in .env first.")
        return 1

    sink = FeishuBitableSink.from_settings(settings)
    try:
        fields = await sink.list_fields()
    finally:
        await sink.aclose()

    print(
        json.dumps(
            {
                "fields": [
                    {
                        "field_id": field.field_id,
                        "field_name": field.field_name,
                        "type_id": field.type_id,
                    }
                    for field in fields
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
