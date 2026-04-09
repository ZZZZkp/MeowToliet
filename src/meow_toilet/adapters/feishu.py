from __future__ import annotations

from meow_toilet.domain.entities import AnalysisResult, PetKitMedia, ScreenshotArtifact


class FeishuBitableSink:
    """Placeholder adapter for Feishu Bitable synchronization."""

    async def upsert_event(
        self,
        media: PetKitMedia,
        analysis: AnalysisResult,
        screenshot: ScreenshotArtifact,
    ) -> None:
        raise NotImplementedError("Upsert event rows and upload screenshot attachments here.")

