# Extractor Pipeline

Imports the latest candidate pairs from flora-extractor into the human-review pool. This is separate from building the final FLoRA release.

[Open interactive guide](../index.html#view=admin&node=admin-maintenance)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- flora-extractor extracted.csv and retired_pairs.csv, previous imported snapshot, configured import thresholds.

## What happens

1. Download and compare a candidate snapshot.
2. Import new/changed pairs and report missing records.
3. Archive and retire explicitly withdrawn untouched records when the retirement policy allows it.

## What is saved, and where

- unvalidated, record_metadata, validation_queue; extractor_snapshots; extractor_maintenance_runs; retired_records for eligible removals.

## When it updates

- Run full sync queues work, which continues on the server if you leave the page.
- History polls about every 3 seconds while a run is active and the page is visible; Refresh history requests it manually.
- The nightly schedule is 02:00 UTC.

## What comes out

- A refreshed validation pool, audited snapshot, stage counts and complete run log.

## Keep in mind

- A pair missing from extracted.csv is not automatically deleted. Retirement uses the explicit withdrawn-pair list and preserves touched records.
- A second active run is refused. A queued response is not completion.

## Buttons and actions

### [Run full sync](admin-maintenance--run-full-sync.md)

POST /api/admin/maintenance/run with stage=full reserves the full maintenance routine.

**Saves:** extractor_maintenance_runs and the imported/retired data as each stage completes.

**Visible:** Queued first; Entries reflect completed import transactions. Also runs nightly at 02:00 UTC.

### [Refresh history](admin-maintenance--refresh-history.md)

Reloads recent run state and counts.

**Saves:** No imported-data write.

**Visible:** On request.

### [Open full log / stage details](admin-maintenance--open-full-log-stage-details.md)

Fetches the chosen run's complete log and shows stage progress/results.

**Saves:** No database changes.

**Visible:** At click time; an active log is refreshed when its run version changes.

## Find it in the code

- [docs/app.js:7225 — fetchMaintenanceRuns](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L7225) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [docs/app.js:7284 — startMaintenanceRun](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L7284) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:6532 — admin_start_maintenance](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6532) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [extractor_maintenance.py:355 — queue_maintenance_run](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/extractor_maintenance.py#L355) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/extractor_maintenance.py)
