# Run full sync

POST /api/admin/maintenance/run with stage=full reserves the full maintenance routine.

[Open interactive guide](../index.html#view=admin&node=admin-maintenance--run-full-sync)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Extractor Pipeline tab and the record or settings selected there.

## What happens

1. POST /api/admin/maintenance/run with stage=full reserves the full maintenance routine.

## What is saved, and where

- extractor_maintenance_runs and the imported/retired data as each stage completes.

## When it updates

- Queued first; Entries reflect completed import transactions. Also runs nightly at 02:00 UTC.

## Find it in the code

- [docs/app.js:7284 — startMaintenanceRun](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L7284) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:6532 — admin_start_maintenance](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6532) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [extractor_maintenance.py:355 — queue_maintenance_run](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/extractor_maintenance.py#L355) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/extractor_maintenance.py)
