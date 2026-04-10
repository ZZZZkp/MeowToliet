from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from meow_toilet.config import Settings
from meow_toilet.domain.entities import AnalysisResult, PetKitMedia, ScreenshotArtifact

FIELD_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "media_id": ("eventId", "Media ID", "媒体ID", "事件ID"),
    "device_id": ("Device ID", "设备ID", "猫砂盆ID"),
    "event_time": ("时间", "Event Time", "事件时间"),
    "elimination_type": ("排泄类型", "Elimination Type", "如厕类型"),
    "stool_score": ("大便评分", "Stool Score", "便便评分"),
    "stool_shape_note": ("大便描述", "Stool Shape Note", "便便描述"),
    "confidence": ("置信度", "Confidence"),
    "raw_summary": ("原始总结", "Raw Summary", "分析摘要"),
    "screenshot": ("大便照片", "Screenshot", "截图"),
    "source_day": ("来源日期", "Source Day", "源日期"),
}


@dataclass(frozen=True, slots=True)
class FeishuUploadResult:
    file_token: str


@dataclass(frozen=True, slots=True)
class FeishuField:
    field_id: str
    field_name: str
    type_id: int


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
        self._resolved_field_mapping: dict[str, str | None] | None = None

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
        response = await self._client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": self._app_id,
                "app_secret": self._app_secret,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"Feishu tenant access token request failed: {payload}")
        return str(payload["tenant_access_token"])

    async def list_fields(self) -> list[FeishuField]:
        tenant_token = await self._get_tenant_access_token()
        return await self._list_fields_with_token(tenant_access_token=tenant_token)

    async def _list_fields_with_token(self, *, tenant_access_token: str) -> list[FeishuField]:
        response = await self._client.get(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self._app_token}/tables/{self._table_id}/fields",
            headers={
                "Authorization": f"Bearer {tenant_access_token}",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"Feishu field listing failed: {payload}")
        items = payload.get("data", {}).get("items", [])
        return [
            FeishuField(
                field_id=str(item["field_id"]),
                field_name=str(item["field_name"]),
                type_id=int(item["type"]),
            )
            for item in items
        ]

    async def _resolve_field_mapping(self, *, tenant_access_token: str) -> dict[str, str | None]:
        if self._resolved_field_mapping is not None:
            return self._resolved_field_mapping

        available_fields = await self._list_fields_with_token(
            tenant_access_token=tenant_access_token,
        )
        available_names = {field.field_name for field in available_fields}
        resolved_mapping: dict[str, str | None] = {}
        for logical_name in self._field_mapping:
            configured_name = self._field_mapping.get(logical_name)
            resolved_mapping[logical_name] = self._pick_field_name(
                configured_name=configured_name,
                aliases=FIELD_NAME_ALIASES.get(logical_name, ()),
                available_names=available_names,
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
            response = await self._client.post(
                "https://open.feishu.cn/open-apis/drive/v1/medias/upload_all",
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
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"Feishu media upload failed: {payload}")
        return FeishuUploadResult(file_token=str(payload["data"]["file_token"]))

    async def _create_record(
        self,
        *,
        fields: dict[str, Any],
        tenant_access_token: str,
    ) -> str:
        response = await self._client.post(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self._app_token}/tables/{self._table_id}/records",
            headers={
                "Authorization": f"Bearer {tenant_access_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={"fields": fields},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"Feishu record creation failed: {payload}")
        return str(payload["data"]["record"]["record_id"])

    def _build_fields(
        self,
        *,
        media: PetKitMedia,
        analysis: AnalysisResult,
        mapping: dict[str, str | None],
        screenshot_token: str,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        self._set_field(fields, mapping.get("media_id"), media.id)
        self._set_field(fields, mapping.get("device_id"), media.device_id)
        self._set_field(fields, mapping.get("event_time"), analysis.event_time.isoformat())
        self._set_field(fields, mapping.get("elimination_type"), analysis.elimination_type.value)
        self._set_field(fields, mapping.get("stool_score"), analysis.stool_score)
        self._set_field(fields, mapping.get("stool_shape_note"), analysis.stool_shape_note)
        self._set_field(fields, mapping.get("confidence"), analysis.confidence)
        self._set_field(fields, mapping.get("raw_summary"), analysis.raw_summary)
        self._set_field(fields, mapping.get("source_day"), media.source_day)
        self._set_field(fields, mapping.get("screenshot"), [{"file_token": screenshot_token}])
        return fields

    @staticmethod
    def _set_field(fields: dict[str, Any], field_name: str | None, value: Any) -> None:
        if not field_name or value is None:
            return
        fields[field_name] = value

    @staticmethod
    def _pick_field_name(
        *,
        configured_name: str | None,
        aliases: tuple[str, ...],
        available_names: set[str],
    ) -> str | None:
        candidates: list[str] = []
        if configured_name:
            candidates.append(configured_name)
        for alias in aliases:
            if alias not in candidates:
                candidates.append(alias)
        for candidate in candidates:
            if candidate in available_names:
                return candidate
        return None
