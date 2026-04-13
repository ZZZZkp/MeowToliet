from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import time
from typing import Any

import httpx
import structlog

from meow_toilet.config import Settings
from meow_toilet.domain.entities import AnalysisResult, PetKitMedia, ScreenshotArtifact
from meow_toilet.errors import ExternalServiceError

FIELD_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "media_id": ("eventId", "Media ID", "媒体ID", "事件ID"),
    "device_id": ("Device ID", "设备ID", "猫砂盆ID"),
    "event_time": ("时间", "Event Time", "事件时间"),
    "pet_name": ("猫", "Pet Name", "宠物名称"),
    "elimination_type": ("排泄类型", "Elimination Type", "如厕类型"),
    "stool_score": ("大便评分", "Stool Score", "便便评分"),
    "stool_shape_note": ("大便描述", "Stool Shape Note", "便便描述"),
    "confidence": ("置信度", "Confidence"),
    "raw_summary": ("原始总结", "Raw Summary", "分析摘要"),
    "screenshot": ("大便照片", "Screenshot", "截图"),
    "source_day": ("来源日期", "Source Day", "源日期"),
}

SINGLE_SELECT_VALUE_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "elimination_type": {
        "poop": ("大便", "便便", "poop"),
        "pee": ("小便", "尿尿", "尿", "pee"),
        "both": ("大小便", "混合", "both"),
        "unknown": ("看不清", "未知", "unknown"),
    },
}


@dataclass(frozen=True, slots=True)
class FeishuUploadResult:
    file_token: str


@dataclass(frozen=True, slots=True)
class FeishuFieldOption:
    option_id: str
    name: str


@dataclass(frozen=True, slots=True)
class FeishuField:
    field_id: str
    field_name: str
    type_id: int
    ui_type: str | None = None
    options: tuple[FeishuFieldOption, ...] = ()


class FeishuBitableSink:
    """将截图附件和结构化字段写入飞书多维表格。"""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        app_token: str,
        table_id: str,
        field_mapping: dict[str, str],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._app_token = app_token
        self._table_id = table_id
        self._field_mapping = field_mapping
        self._client = client or httpx.AsyncClient(timeout=120.0)
        self._owns_client = client is None
        self._resolved_field_mapping: dict[str, FeishuField | None] | None = None
        self._logger = structlog.get_logger(__name__).bind(service="feishu", table_id=table_id)

    @classmethod
    def from_settings(cls, settings: Settings) -> FeishuBitableSink:
        return cls(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            app_token=settings.feishu_bitable_app_token,
            table_id=settings.feishu_bitable_table_id,
            field_mapping=settings.feishu_field_mapping,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def upsert_event(
        self,
        media: PetKitMedia,
        analysis: AnalysisResult,
        screenshot: ScreenshotArtifact,
    ) -> str | None:
        tenant_token = await self._get_tenant_access_token()
        resolved_mapping = await self._resolve_field_mapping(tenant_access_token=tenant_token)
        screenshot_upload = await self._upload_media(
            file_path=screenshot.path,
            tenant_access_token=tenant_token,
            parent_type="bitable_image",
        )
        fields = self._build_fields(
            media=media,
            analysis=analysis,
            mapping=resolved_mapping,
            screenshot_token=screenshot_upload.file_token,
        )
        return await self._create_record(fields=fields, tenant_access_token=tenant_token)

    async def _get_tenant_access_token(self) -> str:
        response = await self._request(
            operation="tenant_access_token",
            method="POST",
            url="https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": self._app_id,
                "app_secret": self._app_secret,
            },
        )
        payload = self._parse_json_response(response, operation="tenant_access_token")
        if payload.get("code") != 0:
            raise self._business_error(
                operation="tenant_access_token",
                payload=payload,
                response=response,
            )
        return str(payload["tenant_access_token"])

    async def list_fields(self) -> list[FeishuField]:
        tenant_token = await self._get_tenant_access_token()
        return await self._list_fields_with_token(tenant_access_token=tenant_token)

    async def _list_fields_with_token(self, *, tenant_access_token: str) -> list[FeishuField]:
        response = await self._request(
            operation="list_fields",
            method="GET",
            url=(
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/"
                f"{self._app_token}/tables/{self._table_id}/fields"
            ),
            headers={
                "Authorization": f"Bearer {tenant_access_token}",
            },
        )
        payload = self._parse_json_response(response, operation="list_fields")
        if payload.get("code") != 0:
            raise self._business_error(
                operation="list_fields",
                payload=payload,
                response=response,
            )
        items = payload.get("data", {}).get("items", [])
        return [
            FeishuField(
                field_id=str(item["field_id"]),
                field_name=str(item["field_name"]),
                type_id=int(item["type"]),
                ui_type=str(item.get("ui_type")) if item.get("ui_type") is not None else None,
                options=tuple(
                    FeishuFieldOption(
                        option_id=str(option["id"]),
                        name=str(option["name"]),
                    )
                    for option in (item.get("property") or {}).get("options", [])
                ),
            )
            for item in items
        ]

    async def _resolve_field_mapping(
        self,
        *,
        tenant_access_token: str,
    ) -> dict[str, FeishuField | None]:
        if self._resolved_field_mapping is not None:
            return self._resolved_field_mapping

        available_fields = await self._list_fields_with_token(
            tenant_access_token=tenant_access_token,
        )
        available_by_name = {field.field_name: field for field in available_fields}
        resolved_mapping: dict[str, FeishuField | None] = {}
        for logical_name in self._field_mapping:
            configured_name = self._field_mapping.get(logical_name)
            resolved_mapping[logical_name] = self._pick_field(
                configured_name=configured_name,
                aliases=FIELD_NAME_ALIASES.get(logical_name, ()),
                available_fields=available_by_name,
            )
        self._resolved_field_mapping = resolved_mapping
        return resolved_mapping

    async def _upload_media(
        self,
        *,
        file_path: Path,
        tenant_access_token: str,
        parent_type: str,
    ) -> FeishuUploadResult:
        with file_path.open("rb") as file_handle:
            response = await self._request(
                operation="upload_media",
                method="POST",
                url="https://open.feishu.cn/open-apis/drive/v1/medias/upload_all",
                headers={
                    "Authorization": f"Bearer {tenant_access_token}",
                },
                data={
                    "file_name": file_path.name,
                    "parent_type": parent_type,
                    "parent_node": self._app_token,
                    "size": str(file_path.stat().st_size),
                },
                files={
                    "file": (file_path.name, file_handle, "image/jpeg"),
                },
            )
        payload = self._parse_json_response(response, operation="upload_media")
        if payload.get("code") != 0:
            raise self._business_error(
                operation="upload_media",
                payload=payload,
                response=response,
            )
        return FeishuUploadResult(file_token=str(payload["data"]["file_token"]))

    async def _create_record(
        self,
        *,
        fields: dict[str, Any],
        tenant_access_token: str,
    ) -> str:
        response = await self._request(
            operation="create_record",
            method="POST",
            url=(
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/"
                f"{self._app_token}/tables/{self._table_id}/records"
            ),
            headers={
                "Authorization": f"Bearer {tenant_access_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={"fields": fields},
        )
        payload = self._parse_json_response(response, operation="create_record")
        if payload.get("code") != 0:
            raise self._business_error(
                operation="create_record",
                payload=payload,
                response=response,
            )
        return str(payload["data"]["record"]["record_id"])

    async def _request(
        self,
        *,
        operation: str,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        started = time.perf_counter()
        try:
            response = await self._client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except httpx.TimeoutException as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            self._logger.warning(
                "external_request_timeout",
                operation=operation,
                elapsed_ms=elapsed_ms,
                url=url,
                error=str(exc),
            )
            raise ExternalServiceError(
                service="feishu",
                operation=operation,
                kind="timeout",
                retryable=True,
                message="Feishu request timed out.",
            ) from exc
        except httpx.HTTPStatusError as exc:
            response = exc.response
            status_code = response.status_code
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            request_id = self._extract_request_id(response)
            response_excerpt = self._response_excerpt(response)
            retryable = status_code == 429 or status_code >= 500
            kind = "rate_limit" if status_code == 429 else "http_status"
            self._logger.warning(
                "external_request_failed",
                operation=operation,
                elapsed_ms=elapsed_ms,
                url=str(response.request.url),
                status_code=status_code,
                request_id=request_id,
                retry_after=response.headers.get("Retry-After"),
                response_excerpt=response_excerpt,
            )
            raise ExternalServiceError(
                service="feishu",
                operation=operation,
                kind=kind,
                retryable=retryable,
                message="Feishu request failed.",
                status_code=status_code,
                request_id=request_id,
            ) from exc
        except httpx.RequestError as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            self._logger.warning(
                "external_request_error",
                operation=operation,
                elapsed_ms=elapsed_ms,
                url=url,
                error=str(exc),
            )
            raise ExternalServiceError(
                service="feishu",
                operation=operation,
                kind="network",
                retryable=True,
                message="Feishu request failed before receiving a response.",
            ) from exc

    def _parse_json_response(
        self,
        response: httpx.Response,
        *,
        operation: str,
    ) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise self._format_error(
                operation=operation,
                message="Feishu returned invalid JSON.",
                response=response,
            ) from exc
        if not isinstance(payload, dict):
            raise self._format_error(
                operation=operation,
                message="Feishu returned a non-object JSON payload.",
                response=response,
            )
        return payload

    def _format_error(
        self,
        *,
        operation: str,
        message: str,
        response: httpx.Response,
    ) -> ExternalServiceError:
        request_id = self._extract_request_id(response)
        self._logger.warning(
            "external_response_format_error",
            operation=operation,
            status_code=response.status_code,
            request_id=request_id,
            response_excerpt=self._response_excerpt(response),
            error=message,
        )
        return ExternalServiceError(
            service="feishu",
            operation=operation,
            kind="response_format",
            retryable=True,
            message=message,
            status_code=response.status_code,
            request_id=request_id,
        )

    def _business_error(
        self,
        *,
        operation: str,
        payload: dict[str, Any],
        response: httpx.Response,
    ) -> ExternalServiceError:
        code = payload.get("code")
        msg = str(payload.get("msg") or payload.get("message") or "Feishu business request failed.")
        request_id = self._extract_request_id(response)
        retryable = self._is_retryable_business_error(code=code, message=msg)
        kind = (
            "rate_limit"
            if retryable and self._looks_like_rate_limit(code=code, message=msg)
            else "business_error"
        )
        self._logger.warning(
            "external_business_error",
            operation=operation,
            status_code=response.status_code,
            request_id=request_id,
            business_code=code,
            retryable=retryable,
            message=msg,
            response_excerpt=self._response_excerpt(response),
        )
        return ExternalServiceError(
            service="feishu",
            operation=operation,
            kind=kind,
            retryable=retryable,
            message=msg,
            status_code=response.status_code,
            request_id=request_id,
        )

    @staticmethod
    def _looks_like_rate_limit(*, code: Any, message: str) -> bool:
        normalized_message = message.lower()
        return (
            str(code) in {"429", "1254290", "99991400", "99991663"}
            or "rate" in normalized_message
            or "too many" in normalized_message
            or "频率" in message
            or "限流" in message
        )

    @classmethod
    def _is_retryable_business_error(cls, *, code: Any, message: str) -> bool:
        return cls._looks_like_rate_limit(code=code, message=message) or bool(
            re.search(r"(timeout|temporar|try again|繁忙|超时)", message, flags=re.IGNORECASE),
        )

    @staticmethod
    def _extract_request_id(response: httpx.Response) -> str | None:
        for header_name in ("x-request-id", "x-tt-logid"):
            if response.headers.get(header_name):
                return response.headers[header_name]
        return None

    @staticmethod
    def _response_excerpt(response: httpx.Response, limit: int = 400) -> str | None:
        try:
            text = response.text.strip()
        except Exception:
            return None
        if not text:
            return None
        return text[:limit]

    def _build_fields(
        self,
        *,
        media: PetKitMedia,
        analysis: AnalysisResult,
        mapping: dict[str, FeishuField | None],
        screenshot_token: str,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        self._set_field(fields, mapping.get("media_id"), media.id, logical_name="media_id")
        self._set_field(fields, mapping.get("device_id"), media.device_id, logical_name="device_id")
        self._set_field(
            fields,
            mapping.get("event_time"),
            analysis.event_time.isoformat(),
            logical_name="event_time",
        )
        self._set_field(fields, mapping.get("pet_name"), media.pet_name, logical_name="pet_name")
        self._set_field(
            fields,
            mapping.get("elimination_type"),
            analysis.elimination_type.value,
            logical_name="elimination_type",
        )
        self._set_field(
            fields,
            mapping.get("stool_score"),
            analysis.stool_score,
            logical_name="stool_score",
        )
        self._set_field(
            fields,
            mapping.get("stool_shape_note"),
            analysis.stool_shape_note,
            logical_name="stool_shape_note",
        )
        self._set_field(
            fields,
            mapping.get("confidence"),
            analysis.confidence,
            logical_name="confidence",
        )
        self._set_field(
            fields,
            mapping.get("raw_summary"),
            analysis.raw_summary,
            logical_name="raw_summary",
        )
        self._set_field(
            fields,
            mapping.get("source_day"),
            media.source_day,
            logical_name="source_day",
        )
        self._set_field(
            fields,
            mapping.get("screenshot"),
            [{"file_token": screenshot_token}],
            logical_name="screenshot",
        )
        return fields

    @staticmethod
    def _set_field(
        fields: dict[str, Any],
        field: FeishuField | None,
        value: Any,
        *,
        logical_name: str,
    ) -> None:
        if field is None or value is None:
            return
        formatted_value = FeishuBitableSink._format_field_value(
            logical_name=logical_name,
            field=field,
            value=value,
        )
        if formatted_value is None:
            return
        fields[field.field_name] = formatted_value

    @staticmethod
    def _pick_field(
        *,
        configured_name: str | None,
        aliases: tuple[str, ...],
        available_fields: dict[str, FeishuField],
    ) -> FeishuField | None:
        candidates: list[str] = []
        if configured_name:
            candidates.append(configured_name)
        for alias in aliases:
            if alias not in candidates:
                candidates.append(alias)
        for candidate in candidates:
            if candidate in available_fields:
                return available_fields[candidate]
        return None

    @staticmethod
    def _format_field_value(
        *,
        logical_name: str,
        field: FeishuField,
        value: Any,
    ) -> Any | None:
        if field.type_id == 3:
            return FeishuBitableSink._format_single_select_value(
                logical_name=logical_name,
                field=field,
                value=value,
            )
        if field.type_id == 1 and not isinstance(value, str):
            return str(value)
        return value

    @staticmethod
    def _format_single_select_value(
        *,
        logical_name: str,
        field: FeishuField,
        value: Any,
    ) -> str | None:
        raw_value = str(value).strip()
        if not raw_value:
            return None

        option_by_normalized_name = {
            FeishuBitableSink._normalize_option_name(option.name): option.name
            for option in field.options
        }
        candidates = [raw_value]
        alias_candidates = SINGLE_SELECT_VALUE_ALIASES.get(logical_name, {}).get(
            FeishuBitableSink._normalize_option_name(raw_value),
            (),
        )
        for candidate in alias_candidates:
            if candidate not in candidates:
                candidates.append(candidate)
        for candidate in candidates:
            normalized = FeishuBitableSink._normalize_option_name(candidate)
            if normalized in option_by_normalized_name:
                return option_by_normalized_name[normalized]
        return None

    @staticmethod
    def _normalize_option_name(value: str) -> str:
        return "".join(value.split()).strip().lower()
