from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
import re
from typing import Any

import httpx

from meow_toilet.config import Settings
from meow_toilet.domain.entities import AnalysisResult, EliminationType, PetKitMedia

GEMINI_FILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "event_offset_seconds": {
            "type": "number",
            "description": "排泄物第一次清晰出现时，从视频开头开始计算的秒数偏移。",
        },
        "elimination_type": {
            "type": "string",
            "enum": ["poop", "pee", "unknown"],
        },
        "stool_shape_note": {
            "type": ["string", "null"],
            "description": (
                "如果是大便，用中文描述是否成型、干燥程度、是否有软便等；"
                "如果看不清则返回“看不清”；如果是小便则返回 null。"
            ),
        },
        "confidence": {
            "type": "number",
            "description": "置信度，范围 0 到 1。",
        },
        "raw_summary": {
            "type": "string",
            "description": "中文简短总结，便于排查问题。",
        },
    },
    "required": [
        "event_offset_seconds",
        "elimination_type",
        "stool_shape_note",
        "confidence",
        "raw_summary",
    ],
}

GEMINI_PROMPT = """你正在分析一段猫砂盆视频。

你必须只返回一个 JSON 对象，且字段名、字段类型必须严格符合给定 schema。
不要输出 markdown、不要输出代码块、不要输出额外解释。

规则：
- event_offset_seconds 必须是排泄物第一次清晰出现的秒数偏移，可以带小数。
- elimination_type 只能是 poop、pee、unknown 之一。
- 如果识别为 pee，stool_shape_note 必须返回 null，不要描述大便情况。
- 如果识别为 poop，stool_shape_note 必须用中文描述大便状态，包括是否成型、干燥程度、是否有软便等。
- 猫的大便正常情况下通常偏干燥；如果明显湿润、发软、软便或不成型，应如实写出。
- 如果看不清、被遮挡、过暗、过糊，无法可靠判断大便状态，则 stool_shape_note 返回“看不清”。
- 如果整体都看不清，elimination_type 返回 unknown，并把 raw_summary 写成简短中文说明。
- confidence 必须在 0 到 1 之间。
- raw_summary 必须是简洁、客观的中文，不要夸张推断。
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
        return self._parse_model_payload("".join(texts))

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
        event_offset_seconds = GeminiAnalyzer._parse_event_offset_seconds(
            payload.get("event_offset_seconds"),
        )
        confidence = GeminiAnalyzer._parse_confidence(payload.get("confidence"))
        event_time = media.started_at + timedelta(seconds=event_offset_seconds)
        stool_score = payload.get("stool_score")
        stool_shape_note = GeminiAnalyzer._normalize_stool_shape_note(
            payload.get("stool_shape_note"),
            elimination_type=elimination_type,
        )
        raw_summary = GeminiAnalyzer._normalize_summary(payload.get("raw_summary"))
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
        normalized = GeminiAnalyzer._normalize_lookup_text(value)
        aliases = {
            "poop": EliminationType.POOP,
            "大便": EliminationType.POOP,
            "便便": EliminationType.POOP,
            "粑粑": EliminationType.POOP,
            "pee": EliminationType.PEE,
            "小便": EliminationType.PEE,
            "尿": EliminationType.PEE,
            "尿尿": EliminationType.PEE,
            "unknown": EliminationType.UNKNOWN,
            "看不清": EliminationType.UNKNOWN,
            "不清楚": EliminationType.UNKNOWN,
            "无法判断": EliminationType.UNKNOWN,
            "无法识别": EliminationType.UNKNOWN,
            "未知": EliminationType.UNKNOWN,
            "both": EliminationType.BOTH,
            "大小便": EliminationType.BOTH,
            "混合": EliminationType.BOTH,
        }
        if normalized in aliases:
            return aliases[normalized]
        try:
            return EliminationType(str(value))
        except ValueError:
            return EliminationType.UNKNOWN

    @staticmethod
    def _parse_model_payload(raw_text: str) -> dict[str, Any]:
        stripped = raw_text.strip()
        payload = GeminiAnalyzer._try_parse_json(stripped)
        if payload is None:
            payload = GeminiAnalyzer._extract_first_json_object(stripped)
        if payload is None:
            payload = GeminiAnalyzer._parse_key_value_lines(stripped)
        if payload is None:
            raise ValueError(f"Gemini response could not be parsed as structured data: {stripped}")
        return GeminiAnalyzer._canonicalize_payload(payload)

    @staticmethod
    def _try_parse_json(text: str) -> dict[str, Any] | None:
        candidates = [text]
        fenced_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if fenced_match:
            candidates.append(fenced_match.group(1))
        for candidate in candidates:
            if not candidate:
                continue
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    @staticmethod
    def _extract_first_json_object(text: str) -> dict[str, Any] | None:
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                parsed, _end = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    @staticmethod
    def _parse_key_value_lines(text: str) -> dict[str, Any] | None:
        key_aliases = {
            "event_offset_seconds": (
                "event_offset_seconds",
                "eventoffsetseconds",
                "排泄时间",
                "排泄物出现时间",
                "出现时间",
                "时间偏移",
                "秒数偏移",
            ),
            "elimination_type": (
                "elimination_type",
                "eliminationtype",
                "排泄类型",
                "类型",
            ),
            "stool_shape_note": (
                "stool_shape_note",
                "stoolshapenote",
                "大便描述",
                "大便状态",
                "便便描述",
                "便便状态",
            ),
            "confidence": (
                "confidence",
                "置信度",
            ),
            "raw_summary": (
                "raw_summary",
                "rawsummary",
                "总结",
                "摘要",
                "说明",
            ),
        }
        lookup = {
            GeminiAnalyzer._normalize_lookup_text(alias): key
            for key, aliases in key_aliases.items()
            for alias in aliases
        }
        payload: dict[str, Any] = {}
        for raw_line in text.splitlines():
            line = raw_line.strip().lstrip("-*").strip()
            if not line or ("：" not in line and ":" not in line):
                continue
            key_text, value_text = re.split(r"\s*[:：]\s*", line, maxsplit=1)
            canonical_key = lookup.get(GeminiAnalyzer._normalize_lookup_text(key_text))
            if canonical_key is None:
                continue
            payload[canonical_key] = value_text.strip().strip("`").strip()
        return payload or None

    @staticmethod
    def _canonicalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
        aliases = {
            "event_offset_seconds": (
                "event_offset_seconds",
                "排泄时间",
                "排泄物出现时间",
                "出现时间",
                "时间偏移",
            ),
            "elimination_type": (
                "elimination_type",
                "排泄类型",
                "类型",
            ),
            "stool_score": (
                "stool_score",
                "大便评分",
            ),
            "stool_shape_note": (
                "stool_shape_note",
                "大便描述",
                "大便状态",
                "便便描述",
                "便便状态",
            ),
            "confidence": (
                "confidence",
                "置信度",
            ),
            "raw_summary": (
                "raw_summary",
                "总结",
                "摘要",
                "说明",
            ),
        }
        normalized_payload = {
            GeminiAnalyzer._normalize_lookup_text(key): value for key, value in payload.items()
        }
        return {
            key: next(
                (
                    normalized_payload[GeminiAnalyzer._normalize_lookup_text(alias)]
                    for alias in supported_aliases
                    if GeminiAnalyzer._normalize_lookup_text(alias) in normalized_payload
                ),
                None,
            )
            for key, supported_aliases in aliases.items()
        }

    @staticmethod
    def _parse_event_offset_seconds(value: Any) -> float:
        if isinstance(value, (int, float)):
            return max(0.0, float(value))
        text = str(value or "").strip()
        if not text:
            return 0.0
        time_text = text.removesuffix("秒").strip()
        if ":" in time_text:
            try:
                total = 0.0
                for segment in time_text.split(":"):
                    total = (total * 60) + float(segment)
                return max(0.0, total)
            except ValueError:
                pass
        match = re.search(r"-?\d+(?:\.\d+)?", time_text)
        if not match:
            return 0.0
        return max(0.0, float(match.group(0)))

    @staticmethod
    def _parse_confidence(value: Any) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = 0.0
        return min(1.0, max(0.0, parsed))

    @staticmethod
    def _normalize_stool_shape_note(
        value: Any,
        *,
        elimination_type: EliminationType,
    ) -> str | None:
        if value is None:
            return "看不清" if elimination_type == EliminationType.UNKNOWN else None
        text = str(value).strip()
        if not text:
            return "看不清" if elimination_type == EliminationType.UNKNOWN else None
        normalized = GeminiAnalyzer._normalize_lookup_text(text)
        if elimination_type == EliminationType.PEE:
            return None
        if elimination_type == EliminationType.UNKNOWN and normalized in {
            "null",
            "none",
            "n/a",
            "na",
            "无",
            "不适用",
            "无需描述",
        }:
            return "看不清"
        if normalized in {"null", "none", "n/a", "na", "无", "不适用", "无需描述"}:
            return None
        return text

    @staticmethod
    def _normalize_summary(value: Any) -> str:
        text = str(value or "").strip()
        return text or "看不清，无法稳定判断排泄情况。"

    @staticmethod
    def _normalize_lookup_text(value: Any) -> str:
        return re.sub(r"[\s_\-]+", "", str(value or "").strip()).lower()
