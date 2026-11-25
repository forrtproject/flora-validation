# When updates happen

Saving, refreshing sources, preparing a snapshot, checking it and committing GitHub are separate events. All configured daily schedules use UTC.

[Open interactive guide](../index.html#view=overview&node=schedule-updates)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- Admin actions, the app's running scheduler and enabled GitHub Actions workflows.

## What happens

1. 00:22 UTC: retry eligible failed Gemini tiebreakers in the app.
2. 02:00 UTC: run extractor maintenance in the app.
3. 03:00 UTC: GitHub Sync entry sheets refreshes sources, prepares/stores the combined FLoRA dataset and commits a successful release.
4. After that workflow completes: Publish FLoRA exports the current stored data, including corrections, to the repository.
5. 04:00 UTC: GitHub Daily validated export runs; the app's network entry checks also become due for the day.
6. Continuously: dispatch source jobs every 5 seconds, extractor jobs every 10 seconds, check entry-check due status every 5 minutes, reap stale validator slots every 2 minutes and run housekeeping hourly.

## What is saved, and where

- Each job saves to its own run/audit tables and/or output files as described on its detail card.

## When it updates

- For Berlin on 29 October 2026 (CET), add one hour to UTC: 02:00 → 03:00, 03:00 → 04:00, 04:00 → 05:00. During CEST add two hours.
- Manual buttons can run the relevant job earlier; browser refresh only reloads data and does not run a pipeline.
- A queue polling interval is not a promised completion time.

## What comes out

- A predictable update sequence with separate evidence for each successful or failed stage.

## Keep in mind

- These are schedules found in code, not a live verification that deployment workers or GitHub jobs are currently healthy.
- The app scheduler must be running for in-process jobs; external network and job runtime can delay completion.
- Public HTTP caches can delay a visible API update even after a successful database save.

## Find it in the code

- [app.py:8052 — _start_scheduler](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L8052) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
- [app.py:7805 — _retry_tiebreakers](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L7805) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
- [entry_checks.py:58 — schedule constants](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/entry_checks.py#L58) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/entry_checks.py)
- [.github/workflows/sync-sources.yml:3 — 03:00 UTC cron](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/.github/workflows/sync-sources.yml#L3) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/.github/workflows/sync-sources.yml)
- [.github/workflows/daily-export.yml:3 — 04:00 UTC cron](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/.github/workflows/daily-export.yml#L3) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/.github/workflows/daily-export.yml)
- [.github/workflows/publish-flora.yml:13 — publish triggers](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/.github/workflows/publish-flora.yml#L13) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/.github/workflows/publish-flora.yml)
