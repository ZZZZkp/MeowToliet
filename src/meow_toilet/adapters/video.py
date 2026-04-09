from __future__ import annotations

import asyncio
import json
from pathlib import Path


class FfmpegVideoProcessor:
    """Use ffmpeg and ffprobe to normalize video and capture screenshots."""

    def __init__(
        self,
        *,
        ffmpeg_binary: str = "ffmpeg",
        ffprobe_binary: str = "ffprobe",
    ) -> None:
        self._ffmpeg_binary = ffmpeg_binary
        self._ffprobe_binary = ffprobe_binary

    async def decode(self, encrypted_video: Path) -> Path:
        output_path = encrypted_video.with_name("decoded.mp4")
        await self._run_command(
            [
                self._ffmpeg_binary,
                "-y",
                "-i",
                str(encrypted_video),
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
        )
        return output_path

    async def capture_cover_frame(
        self,
        decoded_video: Path,
        second_offset: float,
        destination: Path,
    ) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        await self._run_command(
            [
                self._ffmpeg_binary,
                "-y",
                "-ss",
                f"{max(0.0, second_offset):.3f}",
                "-i",
                str(decoded_video),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(destination),
            ],
        )
        return destination

    async def probe_duration(self, video_path: Path) -> float:
        output = await self._run_command(
            [
                self._ffprobe_binary,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(video_path),
            ],
        )
        payload = json.loads(output)
        duration = payload.get("format", {}).get("duration")
        if duration is None:
            raise ValueError(f"ffprobe did not return duration for {video_path}.")
        return float(duration)

    async def _run_command(self, args: list[str]) -> str:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                f"Command failed with exit code {process.returncode}: {' '.join(args)}\n"
                f"{stderr.decode('utf-8', errors='replace')}",
            )
        return stdout.decode("utf-8", errors="replace")

