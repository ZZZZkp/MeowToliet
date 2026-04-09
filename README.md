# MeowToliet

PetKit -> Gemini -> Feishu pipeline for cat litter events.

## Current status

This repository has completed the main `Phase 0` verification work:

- PetKit login, device discovery, and historical media lookup are working.
- Temporary video download and decryption are working.
- `ffmpeg` normalization, duration probing, and screenshot extraction are working.
- Gemini video analysis with structured JSON output is working.
- Feishu Bitable record creation with screenshot attachment is working.
- The repository has been initialized, committed, and pushed to GitHub.

## Planned flow

1. Login to PetKit and keep the session alive.
2. Poll two litter boxes for new media metadata.
3. Download media only when needed into a temporary workspace.
4. Decode video, ask Gemini for elimination details, and capture a screenshot.
5. Delete temporary media after the result and screenshot are persisted.
6. Sync structured data and the screenshot into Feishu Bitable.

## What is in the repo now

- `src/meow_toilet/adapters/petkit.py`: PetKit adapter and historical-media probe support.
- `src/meow_toilet/adapters/video.py`: `ffmpeg` and `ffprobe` based media processing.
- `src/meow_toilet/adapters/gemini.py`: Gemini Files API upload and structured output parsing.
- `src/meow_toilet/adapters/feishu.py`: Feishu tenant token, image upload, and Bitable record creation.
- `src/meow_toilet/phase0_*.py`: real probe commands for each integration stage.
- `tests/`: isolated tests for config, pipeline, PetKit mapping, video processing, Gemini, and Feishu.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn meow_toilet.app.main:app --reload
```

## Verified probe commands

After filling `.env`, run:

```bash
PYTHONPATH=src python3 -m meow_toilet.phase0_petkit --source-day 2026-04-09
PYTHONPATH=src python3 -m meow_toilet.phase0_petkit --source-day 2026-04-09 --download-sample
PYTHONPATH=src python3 -m meow_toilet.phase0_media --source-day 2026-04-09 --output-dir ./tmp/phase0
PYTHONPATH=src python3 -m meow_toilet.phase0_gemini --source-day 2026-04-09 --output-dir ./tmp/gemini
PYTHONPATH=src python3 -m meow_toilet.phase0_feishu_fields
PYTHONPATH=src python3 -m meow_toilet.phase0_feishu --source-day 2026-04-09 --output-dir ./tmp/feishu
```

## Current Feishu table

The current Bitable fields discovered from the configured table are:

- `eventId`
- `时间`
- `猫`
- `大便描述`
- `大便照片`

The current successful write path uses these mappings:

```env
FEISHU_FIELD_MEDIA_ID=eventId
FEISHU_FIELD_EVENT_TIME=时间
FEISHU_FIELD_STOOL_SHAPE_NOTE=大便描述
FEISHU_FIELD_SCREENSHOT=大便照片
```

Other Feishu field mappings can stay empty until matching columns are added to the table.

## Latest validated outcomes

- PetKit sample media probe succeeded for `2026-04-09`.
- Media processing probe succeeded and produced a screenshot from media `105874_1775664690`.
- Gemini probe succeeded and returned structured stool analysis for media `105874_1775664690`.
- Feishu probe succeeded and created record `recvghw46rGWBG`.

## Next priorities

- Persist event metadata and job state in PostgreSQL.
- Turn the Phase 0 commands into scheduler and worker jobs.
- Expand the dashboard beyond configuration status into queue, cover images, and replay tools.
- Evolve the Feishu table schema so more structured fields can be written directly.
