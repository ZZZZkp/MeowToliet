# Runtime Blueprint

## Processes to implement

- `web`: FastAPI dashboard plus APIs.
- `scheduler`: long-lived polling loop that talks to PetKit, persists previews, enqueues tasks, and records last successful poll time.
- `worker`: long-lived processing loop that runs analysis first and Feishu sync second.
- `postgres`: primary persistent task database in Docker deployments.
- `redis`: queue/dispatch backend for background execution.

## Runtime sequence

1. `PetKitPollingScheduler.poll()` calls `PetKitApiAdapter.ensure_session()`, `list_devices()`, and `list_media()`.
2. Each discovered media item becomes a task via `MediaTaskStore.enqueue_media()`.
3. Scheduler attempts `download_cover_image()` and persists the decrypted preview through `PersistentArtifactStore.persist_preview()`.
4. Dispatcher optionally pushes the task ID into ARQ.
5. Worker claims the task from `SqlAlchemyMediaTaskStore`.
6. `LitterEventPipeline.run()` downloads the encrypted video into `TemporaryMediaStore`, decodes it, sends it to Gemini, captures a screenshot, and persists the screenshot.
7. `mark_succeeded()` stores structured analysis plus screenshot path and marks Feishu sync as pending.
8. Feishu sync runs afterward through `FeishuSyncService` and updates sync-specific columns in task state.
9. Dashboard renders recent tasks from SQL state and serves local previews or a placeholder when preview bytes are unavailable.

## Scheduling model

- scheduler wake loop:
  - sleep interval: `PETKIT_POLL_CHECK_INTERVAL_SECONDS`
  - real poll cadence: `PETKIT_POLL_INTERVAL_SECONDS`
  - due state persisted in `SCHEDULER_STATE_PATH`
- worker wake loop:
  - sleep interval: `WORKER_IDLE_SLEEP_SECONDS`
  - stale task recovery threshold: `WORKER_STALE_TASK_TIMEOUT_SECONDS`
- retry model:
  - use `WORKER_RETRY_MAX_ATTEMPTS`
  - use exponential backoff from `WORKER_RETRY_BACKOFF_SECONDS`
  - cap at `WORKER_RETRY_MAX_BACKOFF_SECONDS`

## Concrete modules in this repo

- `src/meow_toilet/config.py`
  - All environment variables and default filesystem roots live here.
  - If a new integration knob exists, it usually belongs here first.
- `src/meow_toilet/runtime.py`
  - Chooses SQL vs fallback task store and ARQ vs noop dispatcher.
  - Touch this if startup behavior must degrade gracefully.
- `src/meow_toilet/services/sql_task_store.py`
  - Source of truth for task lifecycle transitions.
  - Update this together with lifecycle tests if statuses or retries change.
- `src/meow_toilet/workers/jobs.py`
  - Owns stale recovery, retry scheduling, and the split between analysis and Feishu sync.
- `src/meow_toilet/scheduler/service.py`
  - Owns poll cadence, device filtering, dedupe, and persisted previews.
- `src/meow_toilet/services/dashboard.py`
  - Owns task snapshot aggregation, preview loading, placeholder generation, and temporary video cache behavior.
- `src/meow_toilet/app/main.py`
  - Owns public HTTP routes. Keep heavy work delegated to services.

## Task state and artifacts

- Task identity is `device_id:media_id`.
- Preview files are persisted under `PREVIEW_ROOT`.
- Screenshots are persisted under `SCREENSHOT_ROOT`.
- Temporary decode workspaces live under `TEMP_MEDIA_ROOT` and must be deleted after pipeline completion.
- Dashboard playback cache is separate from persisted screenshots and previews.

## Test map to mirror in a new implementation

- `tests/test_config.py`: env parsing and settings defaults.
- `tests/test_runtime.py`: database fallback behavior.
- `tests/test_petkit_adapter.py`: PetKit-specific edge cases.
- `tests/test_video_processor.py`: `ffmpeg` wrapper behavior.
- `tests/test_gemini_adapter.py`: structured response parsing and error handling.
- `tests/test_feishu_adapter.py`: field matching, alias resolution, and record payload behavior.
- `tests/test_pipeline.py`: temporary workspace and screenshot persistence.
- `tests/test_sql_task_store.py`: task lifecycle and Feishu sync state machine.
- `tests/test_scheduler_service.py`: poll, dedupe, preview persistence, and due-time behavior.
- `tests/test_worker_jobs.py`: worker retry and stale recovery semantics.
- `tests/test_dashboard.py` and `tests/test_app_controls.py`: dashboard snapshot and API routes.

## Common traps to avoid

- Do not assume `cover_url` is directly browser-safe. The dashboard is meant to serve local decrypted previews.
- Do not couple Feishu write success to analysis success. They are separate stages.
- Do not store videos as long-term artifacts just because playback exists.
- Do not bypass the task store with ad-hoc state in web routes.
- Do not forget container rebuilds for runtime code changes in Docker mode.
