# Project Target

## Corrected product constraints

- Video files are temporary artifacts. Download them only for active processing or explicit playback, then remove them after Gemini analysis and screenshot capture finish.
- The dashboard should show PetKit cover images by default. Do not assume videos stay locally available.
- PetKit media lookup can go back across previous days by adjusting the requested timestamp window.

## System goal

Build a Python service that:

1. Logs in to PetKit and maintains session health safely.
2. Polls two litter boxes for new media metadata and cover images.
3. Downloads encrypted video into a temporary workspace only when needed.
4. Decodes the video, sends it to Gemini, extracts elimination time and Chinese elimination details, captures a screenshot at the detected event second, and removes the temporary video files.
5. Writes screenshot and structured results into Feishu Bitable.
6. Provides a lightweight dashboard for device health, cover image browsing, job state, and replay or retry actions.

## Preferred architecture

- FastAPI web app for dashboard and APIs
- scheduler process for polling and session refresh
- worker process for decode and analysis jobs
- Redis for queueing and locking
- PostgreSQL for persistent metadata and audit trails
- ffmpeg-based media processing

## Phase plan

### Phase 0

- Prove PetKit login, device discovery, media lookup, temporary download, decode, screenshot, and cleanup path.
- Create explicit interfaces and tests around the pipeline before wiring live credentials.

### Phase 1

- Add persistent storage, queueing, scheduler loop, and dashboard basics.

### Phase 2

- Integrate Gemini and Feishu adapters with retries, idempotency, and debugging views.
- Ensure Feishu syncing can handle single-select pet names and elimination types using live option names.

### Phase 3

- Harden operations, add replay tools, and improve the dashboard experience.
