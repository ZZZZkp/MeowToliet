from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Phase0Checklist:
    login_verified: bool = False
    litter_boxes_identified: bool = False
    historical_media_verified: bool = False
    temporary_download_verified: bool = False
    decode_verified: bool = False
    screenshot_verified: bool = False

    def complete(self) -> bool:
        return all(
            (
                self.login_verified,
                self.litter_boxes_identified,
                self.historical_media_verified,
                self.temporary_download_verified,
                self.decode_verified,
                self.screenshot_verified,
            ),
        )
