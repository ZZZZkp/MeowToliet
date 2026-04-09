# Current State

## Validated capabilities

- PetKit login and session validation work.
- Two camera litter boxes are discoverable from the configured account.
- Historical PetKit media lookup by adjusted timestamp works.
- Temporary media download and decryption work.
- `ffmpeg` normalization, duration probing, and screenshot extraction work.
- Gemini Files API upload and structured JSON analysis work.
- Feishu Bitable image upload and record creation work.
- The repository is committed locally and pushed to GitHub.

## Key commands

```bash
PYTHONPATH=src python3 -m meow_toilet.phase0_petkit --source-day 2026-04-09
PYTHONPATH=src python3 -m meow_toilet.phase0_media --source-day 2026-04-09 --output-dir ./tmp/phase0
PYTHONPATH=src python3 -m meow_toilet.phase0_gemini --source-day 2026-04-09 --output-dir ./tmp/gemini
PYTHONPATH=src python3 -m meow_toilet.phase0_feishu_fields
PYTHONPATH=src python3 -m meow_toilet.phase0_feishu --source-day 2026-04-09 --output-dir ./tmp/feishu
```

## Current Feishu fields

- `eventId`
- `时间`
- `猫`
- `大便描述`
- `大便照片`

## Current working field mapping

```env
FEISHU_FIELD_MEDIA_ID=eventId
FEISHU_FIELD_EVENT_TIME=时间
FEISHU_FIELD_STOOL_SHAPE_NOTE=大便描述
FEISHU_FIELD_SCREENSHOT=大便照片
```

Leave the other Feishu field mapping variables empty until matching columns exist.

## Latest real results

- Gemini sample result was successfully produced for media `105874_1775664690`.
- Feishu sample result successfully created record `recvghw46rGWBG`.

## Recommended next work

1. Add persistence for event metadata and job states.
2. Convert probes into scheduler-driven and worker-driven tasks.
3. Improve the dashboard to show queue status, cover images, and replay controls.
4. Expand the Bitable schema so more structured fields can be synced directly.
