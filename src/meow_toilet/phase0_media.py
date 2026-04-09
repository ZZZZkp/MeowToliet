from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from meow_toilet.adapters.petkit import PetKitApiAdapter
from meow_toilet.adapters.video import FfmpegVideoProcessor
from meow_toilet.config import get_settings


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.petkit_credentials_configured:
        print("Missing PetKit credentials. Fill PETKIT_EMAIL and PETKIT_PASSWORD in .env first.")
        return 1

    adapter = PetKitApiAdapter.from_settings(settings)
    processor = FfmpegVideoProcessor()
    try:
        devices = await adapter.list_devices()
        if not devices:
            print(
                json.dumps(
                    {"ok": False, "reason": "No camera-capable litter boxes were returned by PetKit."},
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            return 1

        target_device = devices[0]
        media_items = await adapter.list_media(target_device, args.source_day)
        if not media_items:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "reason": f"No PetKit video media returned for {args.source_day}.",
                        "device_id": target_device.id,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            return 1

        target_media = media_items[0]
        with TemporaryDirectory() as temp_root:
            temp_root_path = Path(temp_root)
            downloaded_path = temp_root_path / "sample.mp4"
            await adapter.download_media(target_media, downloaded_path)
            decoded_path = await processor.decode(downloaded_path)
            duration = await processor.probe_duration(decoded_path)
            screenshot_second = min(args.frame_second, max(0.0, duration / 2))
            screenshot_path = temp_root_path / "frame.jpg"
            await processor.capture_cover_frame(
                decoded_video=decoded_path,
                second_offset=screenshot_second,
                destination=screenshot_path,
            )
            screenshot_copy = Path(args.output_dir) / f"{target_media.id}.jpg"
            screenshot_copy.parent.mkdir(parents=True, exist_ok=True)
            screenshot_copy.write_bytes(screenshot_path.read_bytes())

        print(
            json.dumps(
                {
                    "ok": True,
                    "device_id": target_device.id,
                    "media_id": target_media.id,
                    "source_day": args.source_day,
                    "video_duration_seconds": duration,
                    "screenshot_second": screenshot_second,
                    "screenshot_path": str(screenshot_copy),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        return 0
    finally:
        await adapter.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download one PetKit sample video, normalize it with ffmpeg, and save a screenshot.",
    )
    parser.add_argument(
        "--source-day",
        default=datetime.now().date().isoformat(),
        help="Historical day to query from PetKit, in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--frame-second",
        type=float,
        default=1.0,
        help="Preferred second offset for the screenshot. The command clamps it to the video duration.",
    )
    parser.add_argument(
        "--output-dir",
        default="./tmp/phase0",
        help="Directory where the extracted screenshot should be kept after temporary files are cleaned up.",
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
