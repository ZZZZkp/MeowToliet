---
name: meow-toilet-monitor
description: Build and maintain the MeowToliet project for PetKit litter box ingestion, Gemini stool analysis, Feishu Bitable syncing, scheduler and worker design, temporary video handling, dashboard work, and TDD-friendly debugging. Use when Codex is changing code or plans in this repository.
---

# Meow Toilet Monitor

Use this skill when working inside the MeowToliet repository.

## Current baseline

- Treat this repository as past early scaffolding. PetKit, ffmpeg, Gemini, and Feishu all already have working adapters and probe commands.
- Assume the Dockerized `web` dashboard is real and usable, not just a placeholder.
- Preserve the current rule that videos are temporary and screenshots plus structured metadata are the persisted outputs.
- Assume the GitHub repository already exists and `main` is live.

## Core constraints

- Treat PetKit as the source of truth for device metadata, cover images, and downloadable media.
- Remember that PetKit preview images are encrypted assets. Do not assume `cover_url` can be embedded directly in the browser.
- Do not design around permanent video storage. Download video only for active processing or playback, then delete it.
- Persist screenshots, structured analysis results, job states, and audit metadata.
- Prefer cover images in the dashboard. Download the video on demand only when playback or re-analysis is requested.
- Assume older PetKit media can be queried by adjusting timestamps, not only by same-day polling.
- Keep the PetKit session warm and guarded by a single account-level lock.
- Route slow work through background jobs instead of tying it to the web request path.

## Default workflow

1. Verify the local architecture before coding: `web`, `scheduler`, `worker`, `adapters`, `services`, `tests`.
2. Preserve the temporary media lifecycle: download, decode, analyze, screenshot, sync, cleanup.
3. Keep interfaces explicit around PetKit, Gemini, Feishu, and media processing so they can be stubbed in tests.
4. Reuse the existing `phase0_*.py` commands to validate live integrations before widening the architecture.
5. Add or update tests before wiring external services whenever a behavior can be isolated.
6. Favor small retries, idempotent writes, and dedupe keys based on PetKit media identity.
7. Preserve the low-frequency home-use bias: manual controls and light operational flow beat overbuilt automation.

## Data expectations

- Gemini output should be normalized into structured fields:
  - event time
  - event offset seconds
  - elimination type
  - stool score or shape class
  - confidence
  - raw summary for debugging
- Feishu records should include screenshot, event metadata, prompt or model version, and sync status.
- The dashboard should emphasize queue state, device health, cover images, and failure recovery instead of long-term media browsing.

## Important repo facts

- The current Bitable only exposes a small field set, so the Feishu adapter must tolerate partial field mappings.
- The Feishu adapter now resolves configured field names through alias fallback, including Chinese field names.
- `时间` is currently handled as a text value, not a dedicated Feishu date field.
- Dashboard cover delivery should prefer decrypting PetKit previews server-side and only fall back to placeholders when decryption or fetch fails.
- The fastest way to inspect live Feishu columns is `python3 -m meow_toilet.phase0_feishu_fields`.
- The fastest way to verify the whole current chain is `phase0_petkit -> phase0_media -> phase0_gemini -> phase0_feishu`.

## References

- Read [references/project-target.md](references/project-target.md) when you need the full corrected project goal and phase plan.
- Read [references/current-state.md](references/current-state.md) when you need the validated current capabilities, live field names, and next priorities.
