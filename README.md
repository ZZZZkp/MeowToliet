# MeowToliet

PetKit -> Gemini -> Feishu 的猫砂盆事件流水线与轻量看板。

## Current status

This repository has completed the main `Phase 0` verification work and now includes a usable
Dockerized dashboard:

- PetKit login, device discovery, and historical media lookup are working.
- Temporary video download and decryption are working.
- `ffmpeg` normalization, duration probing, and screenshot extraction are working.
- Gemini video analysis with Chinese structured JSON output and fallback parsing is working.
- Feishu Bitable record creation with screenshot attachment and single-select field mapping is working.
- Scheduler and worker can now run as long-lived Docker services.
- Worker failures can now auto-retry with exponential backoff before becoming terminal failures.
- Gemini and Feishu request failures now emit more detailed structured logs for timeout, rate limit, and response-format issues.
- Dashboard and `/api/dashboard` now read from a shared task snapshot flow.
- Manual poll, process-next, and per-task retry actions are wired through the dashboard.
- PetKit preview images are handled as encrypted assets and decrypted server-side before being served.
- Docker `web` can start with PostgreSQL, Redis, Alembic migration, and the live dashboard on port `8000`.
- The repository has been initialized, committed, and pushed to GitHub.

## Planned flow

1. Login to PetKit and keep the session alive.
2. Poll two litter boxes for new media metadata.
3. Download media only when needed into a temporary workspace.
4. Decode video, ask Gemini for elimination details in Chinese, and capture a screenshot.
5. Delete temporary media after the result and screenshot are persisted.
6. Sync structured data and the screenshot into Feishu Bitable.

## What is in the repo now

- `src/meow_toilet/adapters/petkit.py`: PetKit adapter and historical-media probe support.
- `src/meow_toilet/adapters/video.py`: `ffmpeg` and `ffprobe` based media processing.
- `src/meow_toilet/adapters/gemini.py`: Gemini Files API upload, Chinese prompt control, and tolerant structured output parsing.
- `src/meow_toilet/adapters/feishu.py`: Feishu tenant token, image upload, field discovery, and type-aware Bitable record creation.
- `src/meow_toilet/services/dashboard.py`: dashboard snapshot building and runtime cover-image delivery.
- `src/meow_toilet/services/sql_task_store.py`: persistent task storage backed by PostgreSQL or SQLite fallback.
- `src/meow_toilet/services/operations.py`: manual poll and retry actions exposed by the dashboard.
- `src/meow_toilet/app/main.py` and `src/meow_toilet/app/templates/dashboard.html`: FastAPI routes and the lightweight board UI.
- `src/meow_toilet/phase0_*.py`: real probe commands for each integration stage.
- `tests/`: isolated tests for config, dashboard, PetKit reload/decrypt behavior, pipeline, video processing, Gemini, and Feishu.

## Quick start

### Local Python mode

```bash
docker compose up -d postgres redis
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
PYTHONPATH=src python3 -m alembic upgrade head
pytest
uvicorn meow_toilet.app.main:app --reload
```

If PostgreSQL is unavailable during local development, the runtime task store now falls back to
`DATABASE_FALLBACK_URL`, which defaults to `sqlite:///./tmp/meow_toilet.db`.

### Docker dashboard mode

```bash
docker compose up --build -d
open http://127.0.0.1:8000/
```

This now starts `web`, `scheduler`, and `worker` alongside PostgreSQL and Redis. To inspect the
background services:

```bash
docker compose logs -f scheduler worker
```

### Docker test mode

```bash
docker compose run --rm test
```

or:

```bash
make docker-test
```

The `test` service uses a dedicated Docker build target with `.[dev]` installed, starts against the
same Compose PostgreSQL and Redis services, and bind-mounts the repository so local code changes are
picked up immediately without rebuilding the runtime `web` container.

The current dashboard is intentionally optimized for a low-frequency home setup:

- PostgreSQL and Redis are kept for persistent state and dispatch compatibility.
- The UI still supports lightweight manual operation even though scheduler and worker now run in the background.
- Cover images are served on demand and videos are still temporary processing artifacts.

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
- `排泄类型`
- `大便描述`
- `大便照片`

The current successful write path uses these mappings:

```env
FEISHU_FIELD_MEDIA_ID=eventId
FEISHU_FIELD_EVENT_TIME=时间
FEISHU_FIELD_PET_NAME=猫
FEISHU_FIELD_ELIMINATION_TYPE=排泄类型
FEISHU_FIELD_STOOL_SHAPE_NOTE=大便描述
FEISHU_FIELD_SCREENSHOT=大便照片
```

Live field type inspection currently returns:

- `eventId`: `Text`
- `时间`: `Text`
- `猫`: `SingleSelect` with options `翠饼 / 酥酥 / 场长`
- `排泄类型`: `SingleSelect` with options `大便 / 小便 / 看不清`
- `大便描述`: `Text`
- `大便照片`: `Attachment`

Ambiguous Gemini results now map directly to the `看不清` single-select option in `排泄类型`.

## Latest validated outcomes

- PetKit sample media probe succeeded for `2026-04-09`.
- Media processing probe succeeded and produced a screenshot from media `105874_1775664690`.
- Gemini probe succeeded and returned Chinese structured analysis for media `105874_1775664690`.
- Dockerized `phase0_feishu` succeeded for `2026-04-10`, created record `recvgnGuboHj1b`, and wrote:
  - `eventId=105874_1775763951`
  - `时间=2026-04-10T03:46:24.500000+08:00`
  - `猫=翠饼`
  - `排泄类型=小便`
- Docker dashboard can render recent queue items and return decrypted PetKit preview JPEGs such as
  `108228:108228_1775777657`.
- Failed tasks can be retried from the dashboard even after the original in-memory media cache is gone.
- Feishu writes can resolve real table field names via configured names plus Chinese or English aliases, and can map single-select values to live option names.

## Next priorities

- Expand the dashboard with retry timing, richer failure inspection, and replay tools.
- Tighten multi-worker claiming semantics further if we decide to scale beyond a single worker container.
- Evolve the Feishu table schema so more structured fields can be written directly.
