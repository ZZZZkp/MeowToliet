from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess

from meow_toilet.adapters.video import FfmpegVideoProcessor


def test_ffmpeg_processor_normalizes_video_and_captures_frame(tmp_path: Path) -> None:
    source_video = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=25",
            "-t",
            "2",
            "-pix_fmt",
            "yuv420p",
            str(source_video),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    async def run_test() -> None:
        processor = FfmpegVideoProcessor()
        decoded = await processor.decode(source_video)
        duration = await processor.probe_duration(decoded)
        screenshot = await processor.capture_cover_frame(
            decoded_video=decoded,
            second_offset=1.0,
            destination=tmp_path / "frame.jpg",
        )

        assert decoded.exists()
        assert duration > 1.5
        assert screenshot.exists()
        assert screenshot.stat().st_size > 0

    asyncio.run(run_test())
