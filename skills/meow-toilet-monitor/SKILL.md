---
name: meow-toilet-monitor
description: Build and maintain the MeowToliet project for PetKit litter box ingestion, Gemini stool analysis, Feishu Bitable syncing, scheduler and worker design, temporary video handling, dashboard work, and TDD-friendly debugging. Use when Codex is changing code or plans in this repository.
---

# Meow Toilet Monitor

Use this skill when working inside the MeowToliet repository.

## Core constraints

- Treat PetKit as the source of truth for device metadata, cover images, and downloadable media.
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
4. Add or update tests before wiring external services whenever a behavior can be isolated.
5. Favor small retries, idempotent writes, and dedupe keys based on PetKit media identity.

## Data expectations

- Gemini output should be normalized into structured fields:
  - event time
  - elimination type
  - stool score or shape class
  - confidence
  - raw summary for debugging
- Feishu records should include screenshot, event metadata, prompt or model version, and sync status.
- The dashboard should emphasize queue state, device health, cover images, and failure recovery instead of long-term media browsing.

## References

- Read [references/project-target.md](references/project-target.md) when you need the full corrected project goal and phase plan.

