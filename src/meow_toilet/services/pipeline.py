from __future__ import annotations

from meow_toilet.domain.entities import PipelineOutcome, PipelineRequest, ScreenshotArtifact
from meow_toilet.services.interfaces import FeishuSink, PetKitGateway, VideoProcessor, VisionAnalyzer
from meow_toilet.services.temp_files import TemporaryMediaStore


class LitterEventPipeline:
    def __init__(
        self,
        *,
        petkit: PetKitGateway,
        video_processor: VideoProcessor,
        analyzer: VisionAnalyzer,
        feishu: FeishuSink,
        temp_store: TemporaryMediaStore,
    ) -> None:
        self._petkit = petkit
        self._video_processor = video_processor
        self._analyzer = analyzer
        self._feishu = feishu
        self._temp_store = temp_store

    async def run(self, request: PipelineRequest) -> PipelineOutcome:
        async with self._temp_store.allocate(request.media) as workspace:
            await self._petkit.download_media(request.media, workspace.encrypted_video)
            decoded_video = await self._video_processor.decode(workspace.encrypted_video)
            analysis = await self._analyzer.analyze_litter_video(decoded_video, request.media)
            screenshot_file = await self._video_processor.capture_cover_frame(
                decoded_video,
                second_offset=max(0.0, analysis.event_offset_seconds),
                destination=workspace.screenshot_path,
            )
            screenshot = ScreenshotArtifact(
                path=screenshot_file,
                captured_at=analysis.event_time,
            )
            record_id = await self._feishu.upsert_event(request.media, analysis, screenshot)
            return PipelineOutcome(
                media_id=request.media.id,
                screenshot=screenshot,
                analysis=analysis,
                feishu_record_id=record_id,
            )
