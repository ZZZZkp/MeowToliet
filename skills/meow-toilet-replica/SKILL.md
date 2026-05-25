---
name: meow-toilet-replica
description: Implement a system with the same runtime behavior as MeowToliet: a PetKit-to-Gemini-to-Feishu asynchronous pipeline with FastAPI dashboard, poll scheduler, worker loop, SQL task state, persisted previews/screenshots, retries, and manual replay controls. Use when an agent needs to build a similar project from scratch or replicate this repository's functionality.
---

# Meow Toilet Replica

Use this skill when you need to build another project that behaves like MeowToliet. Treat it as an implementation blueprint, not just a repo walkthrough.

## System goal

Implement a service with these behaviors:

1. Poll PetKit litter box devices for historical or recent media metadata.
2. Download and decrypt preview images during polling, then persist them locally.
3. Store every discovered media item as a deduped SQL task.
4. Process each task asynchronously by downloading the encrypted video only when needed.
5. Decode the video with `ffmpeg`, analyze it with Gemini, and capture a screenshot at the detected event time.
6. Persist structured analysis and screenshot path before syncing to Feishu.
7. Sync the structured event plus screenshot into Feishu Bitable as a second stage.
8. Expose a FastAPI dashboard with queue state, preview images, manual poll/process/retry controls, and short-lived on-demand playback.

## Non-negotiables

- Treat videos as temporary artifacts. Do not redesign around permanent video storage.
- Persist screenshots, task state, preview images, retry metadata, and Feishu sync state.
- PetKit cover assets are encrypted. The browser should read local persisted previews, not raw PetKit URLs.
- Keep the two-stage lifecycle intact:
  - stage 1: analysis pipeline
  - stage 2: Feishu sync
- Preserve task dedupe identity as `device_id:media_id`.
- Keep adapters and services separable so tests can stub them independently.
- Respect the low-frequency home deployment bias. Manual controls and lightweight operations matter more than heavyweight platform abstractions.
- Runtime may fall back from PostgreSQL to SQLite when the primary database is unavailable.

## Required runtime pieces

Implement these pieces even if filenames differ:

- `settings`
  - env-based config for database, Redis, PetKit, Gemini, Feishu, filesystem roots, retry timing, poll timing, and idle timing.
- `domain entities`
  - device, media item, analysis result, screenshot artifact, media task, dashboard snapshot, job status, sync status.
- `PetKit adapter`
  - login, device discovery, historical media lookup, encrypted video download, encrypted preview download and decryption.
- `video processor`
  - `ffmpeg` decode, screenshot extraction, optional duration probe.
- `Gemini adapter`
  - file upload, readiness polling, JSON schema constrained response, tolerant parsing, cleanup.
- `Feishu adapter`
  - tenant token, field discovery, alias resolution, single-select mapping, attachment upload, upsert by media identity.
- `task store`
  - enqueue, claim, success/failure transitions, retry scheduling, stale recovery, Feishu sync transitions.
- `scheduler loop`
  - due-check loop for polling PetKit and enqueueing tasks.
- `worker loop`
  - claim analysis tasks first, then claim pending Feishu sync tasks.
- `dashboard app`
  - queue summary, recent tasks, preview endpoint, video endpoint, manual actions.
- `artifact persistence`
  - store screenshots and previews on local disk.
- `temp workspace`
  - create a per-task temporary directory and always clean it up.

Read [references/repo-map.md](references/repo-map.md) for the concrete component map.

## Runtime flow to implement

Implement the runtime in this order:

1. `scheduler` wake-up loop
   - Wake every `PETKIT_POLL_CHECK_INTERVAL_SECONDS`.
   - Read the last successful poll timestamp from persistent state such as `scheduler_state.json`.
   - If `now < last_successful_poll_at + PETKIT_POLL_INTERVAL_SECONDS`, skip polling.
   - If due, perform a real poll.
2. scheduler poll operation
   - Ensure the PetKit session is valid.
   - List devices and optionally filter by configured device IDs.
   - For each device, query media for the target `source_day`.
   - For each media item:
     - derive `task_id = device_id:media_id`
     - try downloading and decrypting the preview image
     - persist preview under a stable local path
     - enqueue the task into SQL with status `queued`
     - if the task is new, optionally dispatch it to Redis/ARQ
     - if the task already exists, refresh mutable metadata but do not duplicate it
   - Persist `last_successful_poll_at`
3. worker wake-up loop
   - Wake every `WORKER_IDLE_SLEEP_SECONDS`.
   - Recover stale tasks older than `WORKER_STALE_TASK_TIMEOUT_SECONDS`.
   - Try to claim the next analysis task whose status is `queued` and `next_attempt_at <= now`.
   - If no analysis task is available, try to claim the next Feishu sync task whose analysis already succeeded and whose sync state is pending.
4. analysis pipeline
   - Create a temporary workspace under `TEMP_MEDIA_ROOT`.
   - Download the encrypted PetKit video into the workspace.
   - Decode it to a normal MP4 with `ffmpeg`.
   - Upload the decoded video to Gemini.
   - Ask Gemini for a Chinese structured JSON result with:
     - `event_offset_seconds`
     - `elimination_type`
     - `stool_shape_note`
     - `confidence`
     - `raw_summary`
   - Capture a screenshot from the decoded video at `event_offset_seconds`.
   - Persist that screenshot under `SCREENSHOT_ROOT`.
   - Delete the entire temporary workspace.
   - Mark the task `succeeded` and mark Feishu sync `pending`.
5. Feishu sync stage
   - Load the persisted screenshot and structured analysis from SQL state.
   - Resolve Feishu table fields by configured names plus Chinese or English aliases.
   - Map backend enums to live single-select option names.
   - Upload the screenshot as a Feishu attachment.
   - Upsert the record using media ID as the stable identity.
   - Mark Feishu sync `succeeded` and store `feishu_record_id`.
6. dashboard behavior
   - Render queue totals and recent tasks from the SQL task store.
   - Serve preview images from local persisted preview files.
   - If a preview file is missing, return a placeholder image rather than failing the whole page.
   - Offer manual endpoints for:
     - poll once
     - process next task
     - process a specific task
     - retry failed tasks if your UI includes that control
   - For video playback, download and decode on demand into a short-lived cache, not permanent storage.

## Scheduling and background job rules

- Poll cadence and wake cadence are separate:
  - the scheduler wakes often
  - it polls PetKit only when the persisted due time is reached
- Worker cadence and retry timing are separate:
  - the worker wakes often
  - it only claims tasks whose retry time has arrived
- Analysis and Feishu sync must be two separate stages so Feishu retries do not force Gemini to rerun.
- Persist previews during polling so the dashboard never depends on live PetKit image fetches.
- Persist screenshots during analysis so Feishu retries can reuse them later.
- Use exponential backoff for retryable failures.
- Recover stale `running` tasks back into a retryable state when a worker dies mid-flight.

Read [references/validation-playbook.md](references/validation-playbook.md) for timing knobs and verification commands.

## State machine to reproduce

- analysis task statuses:
  - `queued`
  - `running`
  - `succeeded`
  - `failed`
- Feishu sync statuses:
  - `pending`
  - `running`
  - `succeeded`
  - `failed`
- key persisted fields:
  - media metadata
  - preview path
  - screenshot path
  - attempts
  - last error and error kind
  - next attempt time
  - event time
  - elimination type
  - stool note
  - confidence
  - raw summary
  - Feishu record ID
  - Feishu sync attempts and retry timing

## Implementation order

Build in this order unless the task requires something else:

1. entities and settings
2. task store and state transitions
3. temporary workspace and artifact persistence
4. PetKit adapter and local probe command
5. `ffmpeg` processor and screenshot probe
6. Gemini adapter and structured output probe
7. Feishu adapter and field inspection probe
8. scheduler loop and worker loop
9. FastAPI dashboard and manual controls
10. Docker composition for `web`, `scheduler`, `worker`, `postgres`, and `redis`

## Implementation guidance by component

- PetKit
  - historical lookup matters; do not assume same-day only
  - serialize account-sensitive operations behind a shared lock
  - treat preview and video as separate downloadable assets
- Gemini
  - force structured JSON output
  - tolerate malformed-but-recoverable JSON when practical
  - delete uploaded files after inference
- Feishu
  - schema can drift; resolve fields dynamically
  - single-select columns require real option names, not backend enum strings
- task store
  - own all transitions here rather than scattering them across web handlers
  - support both analysis retries and Feishu sync retries
- dashboard
  - read from stored task state
  - never require raw PetKit credentials in the browser
  - serve playback through a short-lived server-side cache

## Validation ladder

Use this when implementing the project:

- unit-test task lifecycle and retry behavior first
- then test scheduler enqueue and dedupe behavior
- then test pipeline temp cleanup and screenshot persistence
- then test dashboard API behavior
- then run live probe commands for PetKit, Gemini, and Feishu
- then run the full Docker stack

Read [references/validation-playbook.md](references/validation-playbook.md) for the concrete commands.

## Known behavior details to preserve

- The FastAPI app is a control surface, not the heavy-processing engine.
- Manual operations should reuse the same scheduler and worker internals as background execution.
- SQL is the source of truth for queue state, artifacts, and sync state.
- Preview images should survive process restarts because they are persisted on disk and referenced from SQL.
- Video playback is intentionally temporary and should self-clean after a short retention window.
- Gemini should return Chinese summaries and use `unknown` plus `看不清` semantics for ambiguous results.

## References

- Read [references/repo-map.md](references/repo-map.md) for the concrete architecture and runtime ownership.
- Read [references/validation-playbook.md](references/validation-playbook.md) for commands, timing variables, and probe flow.
