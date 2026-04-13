from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from meow_toilet.adapters.feishu import FeishuBitableSink
from meow_toilet.adapters.gemini import GeminiAnalyzer
from meow_toilet.adapters.petkit import PetKitApiAdapter
from meow_toilet.adapters.video import FfmpegVideoProcessor
from meow_toilet.config import get_settings
from meow_toilet.domain.entities import ScreenshotArtifact


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.petkit_credentials_configured:
        print("Missing PetKit credentials. Fill PETKIT_EMAIL and PETKIT_PASSWORD in .env first.")
        return 1
    if not settings.gemini_configured:
        print("Missing Gemini API key. Fill GEMINI_API_KEY in .env first.")
        return 1
    if not settings.feishu_configured:
        print("Missing Feishu credentials. Fill FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_BITABLE_APP_TOKEN, and FEISHU_BITABLE_TABLE_ID in .env first.")
        return 1

    petkit = PetKitApiAdapter.from_settings(settings)
    video_processor = FfmpegVideoProcessor()
    analyzer = GeminiAnalyzer.from_settings(settings)
    feishu = FeishuBitableSink.from_settings(settings)
    try:
        devices = await petkit.list_devices()
        if not devices:
            print(json.dumps({"ok": False, "reason": "No PetKit litter devices found."}, ensure_ascii=False, indent=2))
            return 1

        media_items = await petkit.list_media(devices[0], args.source_day)
        if not media_items:
            print(
                json.dumps(
                    {"ok": False, "reason": f"No PetKit video media returned for {args.source_day}."},
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            return 1

        target_media = media_items[0]
        with TemporaryDirectory() as temp_root:
            temp_root_path = Path(temp_root)
            encrypted_path = temp_root_path / "source.mp4"
            await petkit.download_media(target_media, encrypted_path)
            decoded_path = await video_processor.decode(encrypted_path)
            analysis = await analyzer.analyze_litter_video(decoded_path, target_media)
            screenshot_path = temp_root_path / "event.jpg"
            await video_processor.capture_cover_frame(
                decoded_video=decoded_path,
                second_offset=analysis.event_offset_seconds,
                destination=screenshot_path,
            )
            kept_screenshot = Path(args.output_dir) / f"{target_media.id}.jpg"
            kept_screenshot.parent.mkdir(parents=True, exist_ok=True)
            kept_screenshot.write_bytes(screenshot_path.read_bytes())
            record_id = await feishu.upsert_event(
                media=target_media,
                analysis=analysis,
                screenshot=ScreenshotArtifact(
                    path=kept_screenshot,
                    captured_at=analysis.event_time,
                ),
            )

        print(
            json.dumps(
                {
                    "ok": True,
                    "record_id": record_id,
                    "media_id": target_media.id,
                    "source_day": args.source_day,
                    "event_time": FeishuBitableSink._format_event_time(target_media.started_at),
                    "elimination_type": analysis.elimination_type.value,
                    "stool_score": analysis.stool_score,
                    "stool_shape_note": analysis.stool_shape_note,
                    "confidence": analysis.confidence,
                    "screenshot_path": str(kept_screenshot),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        return 0
    finally:
        await petkit.aclose()
        await analyzer.aclose()
        await feishu.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Feishu Bitable Phase 0 probe.")
    parser.add_argument(
        "--source-day",
        default=datetime.now().date().isoformat(),
        help="Historical day to query from PetKit, in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--output-dir",
        default="./tmp/feishu",
        help="Directory where the extracted screenshot should be kept.",
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
