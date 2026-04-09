from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json

from meow_toilet.adapters.petkit import PetKitApiAdapter
from meow_toilet.config import get_settings


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.petkit_credentials_configured:
        print("Missing PetKit credentials. Fill PETKIT_EMAIL and PETKIT_PASSWORD in .env first.")
        return 1

    adapter = PetKitApiAdapter.from_settings(settings)
    try:
        probe = await adapter.run_phase0_probe(
            source_day=args.source_day,
            sample_download=args.download_sample,
        )
    finally:
        await adapter.aclose()

    print(
        json.dumps(
            {
                "can_login": probe.can_login,
                "can_list_devices": probe.can_list_devices,
                "can_query_historical_media": probe.can_query_historical_media,
                "can_download_encrypted_video": probe.can_download_encrypted_video,
                "device_count": probe.device_count,
                "media_count": probe.media_count,
                "sampled_device_id": probe.sampled_device_id,
                "sampled_media_id": probe.sampled_media_id,
                "notes": probe.notes,
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the PetKit Phase 0 probe.")
    parser.add_argument(
        "--source-day",
        default=datetime.now().date().isoformat(),
        help="Historical day to query from PetKit, in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--download-sample",
        action="store_true",
        help="Download and decrypt the first returned video to verify the full Phase 0 path.",
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
