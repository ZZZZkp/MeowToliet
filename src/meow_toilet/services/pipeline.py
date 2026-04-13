from __future__ import annotations

from meow_toilet.domain.entities import PipelineOutcome, PipelineRequest, ScreenshotArtifact
from meow_toilet.services.artifacts import PersistentArtifactStore
from meow_toilet.services.interfaces import PetKitGateway, VideoProcessor, VisionAnalyzer
from meow_toilet.services.temp_files import TemporaryMediaStore


class LitterEventPipeline:
    def __init__(
        self,
        *,
        petkit: PetKitGateway,
        video_processor: VideoProcessor,
        analyzer: VisionAnalyzer,
        temp_store: TemporaryMediaStore,
        artifact_store: PersistentArtifactStore,
    ) -> None:
        self._petkit = petkit
        self._video_processor = video_processor
        self._analyzer = analyzer
        self._temp_store = temp_store
        self._artifact_store = artifact_store

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
            screenshot = await self._artifact_store.persist_screenshot(
                task_id=request.media.dedupe_key,
                source_path=screenshot_file,
                captured_at=analysis.event_time,
            )
            return PipelineOutcome(
                media_id=request.media.id,
                screenshot=screenshot,
                analysis=analysis,
                feishu_record_id=None,
            )
