from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
import os
from pathlib import Path
from typing import Any

import httpx

from meow_toilet.config import Settings
from meow_toilet.domain.entities import AnalysisResult, PetKitMedia, ScreenshotArtifact


@dataclass(frozen=True, slots=True)
class FeishuUploadResult:
    file_token: str


@dataclass(frozen=True, slots=True)
class FeishuField:
    field_id: str
    field_name: str
    type_id: int


class FeishuBitableSink:
    """Feishu Bitable writer for screenshot attachments and structured fields."""

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
        screenshot_upload = await self._upload_media(
            file_path=screenshot.path,
            tenant_access_token=tenant_token,
            parent_type="bitable_image",
        )
        fields = self._build_fields(
            media=media,
            analysis=analysis,
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
        response = await self._client.get(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self._app_token}/tables/{self._table_id}/fields",
            headers={
                "Authorization": f"Bearer {tenant_token}",
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
        screenshot_token: str,
    ) -> dict[str, Any]:
        mapping = self._field_mapping
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
