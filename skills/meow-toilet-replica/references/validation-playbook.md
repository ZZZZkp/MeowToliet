# Validation Playbook

## Minimal build order

```bash
1. task store and entities
2. temp workspace and artifact persistence
3. PetKit polling and preview persistence
4. worker analysis pipeline
5. Feishu sync stage
6. dashboard and manual operations
7. Dockerized multi-process runtime
```

## Important timing variables

```bash
PETKIT_POLL_INTERVAL_SECONDS
PETKIT_POLL_CHECK_INTERVAL_SECONDS
PETKIT_SESSION_REFRESH_SECONDS
WORKER_IDLE_SLEEP_SECONDS
WORKER_STALE_TASK_TIMEOUT_SECONDS
WORKER_RETRY_MAX_ATTEMPTS
WORKER_RETRY_BACKOFF_SECONDS
WORKER_RETRY_MAX_BACKOFF_SECONDS
```

## Python setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
PYTHONPATH=src python3 -m alembic upgrade head
```

If PostgreSQL is not available locally, runtime can fall back to `DATABASE_FALLBACK_URL`, which defaults to `sqlite:///./tmp/meow_toilet.db`.

## Test commands to prioritize

```bash
pytest tests/test_pipeline.py
pytest tests/test_scheduler_service.py
pytest tests/test_sql_task_store.py
pytest tests/test_app_controls.py
pytest
```

## Local runtime commands

```bash
PYTHONPATH=src python3 -m uvicorn meow_toilet.app.main:app --reload
PYTHONPATH=src python3 -m meow_toilet.scheduler.service --loop
PYTHONPATH=src python3 -m meow_toilet.workers.jobs --loop
```

## Docker commands

```bash
docker compose up -d postgres redis
docker compose up --build -d
docker compose logs -f scheduler worker
docker compose run --rm test
```

When runtime code changes need to be reflected in Dockerized services, rebuild `web`, `scheduler`, and `worker` instead of assuming a plain restart is enough.

## Live integration probes

Run these only after `.env` is populated:

```bash
PYTHONPATH=src python3 -m meow_toilet.phase0_petkit --source-day 2026-04-09
PYTHONPATH=src python3 -m meow_toilet.phase0_petkit --source-day 2026-04-09 --download-sample
PYTHONPATH=src python3 -m meow_toilet.phase0_media --source-day 2026-04-09 --output-dir ./tmp/phase0
PYTHONPATH=src python3 -m meow_toilet.phase0_gemini --source-day 2026-04-09 --output-dir ./tmp/gemini
PYTHONPATH=src python3 -m meow_toilet.phase0_feishu_fields
PYTHONPATH=src python3 -m meow_toilet.phase0_feishu --source-day 2026-04-09 --output-dir ./tmp/feishu
```

## Suggested validation order

1. Run the narrowest relevant test file.
2. Confirm temp workspace cleanup and artifact persistence behavior.
3. Confirm scheduler dedupe plus preview persistence behavior.
4. Confirm worker retries and stale recovery behavior.
5. Use a phase-0 probe for each live integration.
6. Use FastAPI or Docker runtime only after code-level validation is green.
