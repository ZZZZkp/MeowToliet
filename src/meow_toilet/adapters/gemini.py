from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx

from meow_toilet.config import Settings
from meow_toilet.domain.entities import AnalysisResult, EliminationType, PetKitMedia

GEMINI_FILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "event_offset_seconds": {
            "type": "number",
            "description": "从视频开始到主要排泄行为发生时刻的秒数偏移。",
        },
        "elimination_type": {
            "type": "string",
            "enum": ["poop", "pee", "both", "unknown"],
        },
        "stool_score": {
            "type": ["string", "null"],
            "description": "若便便清晰可见，返回 1 到 7 的布里斯托分型，否则返回 null。",
        },
        "stool_shape_note": {
            "type": ["string", "null"],
            "description": "仅在能看到便便时填写简短客观描述。",
        },
        "confidence": {
            "type": "number",
            "description": "置信度，范围 0 到 1。",
        },
        "raw_summary": {
            "type": "string",
            "description": "用于排查问题的简短自然语言总结。",
        },
    },
    "required": [
        "event_offset_seconds",
        "elimination_type",
        "stool_score",
        "stool_shape_note",
        "confidence",
        "raw_summary",
    ],
}

GEMINI_PROMPT = """你正在分析一段猫砂盆视频。

只返回符合给定 schema 的结构化 JSON，不要输出额外说明。

规则：
- event_offset_seconds 必须是从视频开头算起的秒数偏移。
- elimination_type 只能是 poop、pee、both、unknown 之一。
- 只有在画面中能够明确识别便便时，stool_score 才返回 1 到 7 的字符串，否则返回 null。
- 只有在能够看到便便时，stool_shape_note 才填写简短、客观、直接的描述，否则返回 null。
- confidence 必须在 0 到 1 之间。
- raw_summary 必须简洁、客观，避免夸张推断。
- 如果画面模糊、被遮挡或者无法确认，请把 elimination_type 设为 unknown，并降低 confidence。
"""


@dataclass(frozen=True, slots=True)
class UploadedGeminiFile:
    name: str
    uri: str
    mime_type: str
    state: str


class GeminiAnalyzer:
    """使用官方 REST API 调用 Gemini 进行视频分析。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: httpx.AsyncClient | None = None,
        poll_interval_seconds: float = 2.0,
        processing_timeout_seconds: float = 120.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=120.0)
        self._owns_client = client is None
        self._poll_interval_seconds = poll_interval_seconds
        self._processing_timeout_seconds = processing_timeout_seconds

    @classmethod
    def from_settings(cls, settings: Settings) -> GeminiAnalyzer:
        return cls(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def analyze_litter_video(
        self,
        decoded_video: Path,
        media: PetKitMedia,
    ) -> AnalysisResult:
        uploaded_file = await self._upload_file(decoded_video)
        try:
            active_file = await self._wait_until_active(uploaded_file.name)
            payload = await self._generate_structured_response(active_file)
        finally:
            await self._delete_file(uploaded_file.name)
        return self._payload_to_result(payload, media)

    async def _upload_file(self, video_path: Path) -> UploadedGeminiFile:
        metadata = {
            "file": {
                "display_name": video_path.name,
            },
        }
        start_response = await self._client.post(
            f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={self._api_key}",
            headers={
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(video_path.stat().st_size),
                "X-Goog-Upload-Header-Content-Type": "video/mp4",
                "Content-Type": "application/json",
            },
            json=metadata,
        )
        start_response.raise_for_status()
        upload_url = start_response.headers.get("X-Goog-Upload-URL")
        if not upload_url:
            raise ValueError("Gemini upload start response did not include X-Goog-Upload-URL.")

        finalize_response = await self._client.post(
            upload_url,
            headers={
                "Content-Length": str(video_path.stat().st_size),
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
            },
            content=video_path.read_bytes(),
        )
        finalize_response.raise_for_status()
        return self._parse_uploaded_file(self._unwrap_file_payload(finalize_response.json()))

    async def _wait_until_active(self, file_name: str) -> UploadedGeminiFile:
        deadline = asyncio.get_running_loop().time() + self._processing_timeout_seconds
        while True:
            response = await self._client.get(
                f"https://generativelanguage.googleapis.com/v1beta/{file_name}?key={self._api_key}",
            )
            response.raise_for_status()
            file_data = self._parse_uploaded_file(self._unwrap_file_payload(response.json()))
            if file_data.state == "ACTIVE":
                return file_data
            if file_data.state == "FAILED":
                raise RuntimeError(f"Gemini file processing failed for {file_name}.")
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for Gemini file {file_name} to become ACTIVE.",
                )
            await asyncio.sleep(self._poll_interval_seconds)

    async def _generate_structured_response(
        self,
        uploaded_file: UploadedGeminiFile,
    ) -> dict[str, Any]:
        response = await self._client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent?key={self._api_key}",
            json={
                "contents": [
                    {
                        "parts": [
                            {
                                "file_data": {
                                    "mime_type": uploaded_file.mime_type,
                                    "file_uri": uploaded_file.uri,
                                },
                            },
                            {
                                "text": GEMINI_PROMPT,
                            },
                        ],
                    },
                ],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": GEMINI_FILE_SCHEMA,
                },
            },
        )
        response.raise_for_status()
        candidates = response.json().get("candidates", [])
        if not candidates:
            raise ValueError("Gemini returned no candidates.")
        parts = candidates[0].get("content", {}).get("parts", [])
        texts = [part.get("text", "") for part in parts if part.get("text")]
        if not texts:
            raise ValueError("Gemini candidate did not include JSON text.")
        return json.loads("".join(texts))

    async def _delete_file(self, file_name: str) -> None:
        response = await self._client.delete(
            f"https://generativelanguage.googleapis.com/v1beta/{file_name}?key={self._api_key}",
        )
        response.raise_for_status()

    @staticmethod
    def _parse_uploaded_file(payload: dict[str, Any]) -> UploadedGeminiFile:
        state = payload.get("state")
        if isinstance(state, dict):
            state = state.get("name", "")
        return UploadedGeminiFile(
            name=str(payload["name"]),
            uri=str(payload["uri"]),
            mime_type=str(payload.get("mimeType", "video/mp4")),
            state=str(state or "STATE_UNSPECIFIED"),
        )

    @staticmethod
    def _unwrap_file_payload(payload: dict[str, Any]) -> dict[str, Any]:
        maybe_file = payload.get("file")
        if isinstance(maybe_file, dict):
            return maybe_file
        return payload

    @staticmethod
    def _payload_to_result(payload: dict[str, Any], media: PetKitMedia) -> AnalysisResult:
        elimination_type = GeminiAnalyzer._parse_elimination_type(payload.get("elimination_type"))
        event_offset_seconds = max(0.0, float(payload.get("event_offset_seconds", 0.0)))
        confidence = min(1.0, max(0.0, float(payload.get("confidence", 0.0))))
        event_time = media.started_at + timedelta(seconds=event_offset_seconds)
        stool_score = payload.get("stool_score")
        stool_shape_note = payload.get("stool_shape_note")
        raw_summary = str(payload.get("raw_summary", "")).strip()
        return AnalysisResult(
            event_time=event_time,
            event_offset_seconds=event_offset_seconds,
            elimination_type=elimination_type,
            stool_score=None if stool_score is None else str(stool_score),
            stool_shape_note=None if stool_shape_note is None else str(stool_shape_note),
            confidence=confidence,
            raw_summary=raw_summary,
        )

    @staticmethod
    def _parse_elimination_type(value: Any) -> EliminationType:
        try:
            return EliminationType(str(value))
        except ValueError:
            return EliminationType.UNKNOWN
