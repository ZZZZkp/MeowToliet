# MeowToliet

PetKit -> Gemini -> Feishu pipeline for cat litter events.

## Current focus

This repository is currently in `Phase 0 + project scaffold`:

- define a testable architecture for PetKit media intake
- enforce temporary media lifecycle instead of permanent video storage
- prepare dashboard, scheduler, worker, and adapter boundaries
- make the pipeline easy to debug with small, isolated tests

## Planned flow

1. Login to PetKit and keep the session alive.
2. Poll two litter boxes for new media metadata.
3. Download media only when needed into a temporary workspace.
4. Decode video, ask Gemini for elimination details, and capture a screenshot.
5. Delete temporary media after the result and screenshot are persisted.
6. Sync structured data and the screenshot into Feishu Bitable.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn meow_toilet.app.main:app --reload
```

## Phase 0 probe

After filling `.env`, run:

```bash
PYTHONPATH=src python3 -m meow_toilet.phase0_petkit --source-day 2026-04-09
PYTHONPATH=src python3 -m meow_toilet.phase0_petkit --source-day 2026-04-09 --download-sample
PYTHONPATH=src python3 -m meow_toilet.phase0_media --source-day 2026-04-09 --output-dir ./tmp/phase0
PYTHONPATH=src python3 -m meow_toilet.phase0_gemini --source-day 2026-04-09 --output-dir ./tmp/gemini
```
