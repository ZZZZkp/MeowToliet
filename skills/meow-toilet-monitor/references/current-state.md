# Current State

## Validated capabilities

- PetKit login and session validation work.
- Two camera litter boxes are discoverable from the configured account.
- Historical PetKit media lookup by adjusted timestamp works.
- Temporary media download and decryption work.
- PetKit preview images can be decrypted into real JPEGs with the record AES key.
- `ffmpeg` normalization, duration probing, and screenshot extraction work.
- Gemini Files API upload, Chinese prompt control, and structured JSON fallback parsing work.
- Feishu Bitable image upload, single-select mapping, and record creation work.
- Feishu field mapping can fall back to real Chinese and English column aliases.
- Dashboard manual actions can poll, process the next task, and retry an individual failed task.
- Docker `web` starts against PostgreSQL and Redis and serves the live dashboard on port `8000`.
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
- `排泄类型`
- `大便描述`
- `大便照片`

## Current working field mapping

```env
FEISHU_FIELD_MEDIA_ID=eventId
FEISHU_FIELD_EVENT_TIME=时间
FEISHU_FIELD_PET_NAME=猫
FEISHU_FIELD_ELIMINATION_TYPE=排泄类型
FEISHU_FIELD_STOOL_SHAPE_NOTE=大便描述
FEISHU_FIELD_SCREENSHOT=大便照片
```

## Current field types and options

- `eventId`: `Text`
- `时间`: `Text`
- `猫`: `SingleSelect` with `翠饼 / 酥酥 / 场长`
- `排泄类型`: `SingleSelect` with `大便 / 小便 / 看不清`
- `大便描述`: `Text`
- `大便照片`: `Attachment`

Leave the other Feishu field mapping variables empty until matching columns exist. Ambiguous detections now map directly into the `看不清` single-select option in `排泄类型`.

## Latest real results

- Gemini sample result was successfully produced for media `105874_1775664690`.
- Dockerized `phase0_feishu` successfully created record `recvgnGuboHj1b` for media `105874_1775763951`.
- That live Feishu record was verified to contain `eventId=105874_1775763951`, `时间=2026-04-10T03:46:24.500000+08:00`, `猫=翠饼`, and `排泄类型=小便`.
- Preview payload `108228:108228_1775777657` was verified to decrypt from raw PetKit bytes into a `528x528` JPEG.

## Recommended next work

1. Turn the current manual dashboard actions into long-running scheduler and worker loops.
2. Add replay and richer investigation tools on top of the existing cover and queue board.
3. Expand the Bitable schema so more structured fields can be synced directly.
4. Decide whether background workers should remain optional in low-frequency home mode.
