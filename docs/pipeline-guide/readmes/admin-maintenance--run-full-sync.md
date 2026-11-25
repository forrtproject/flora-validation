# Run full sync

POST /api/admin/maintenance/run with stage=full reserves the full maintenance routine.

[Open interactive guide](../index.html#view=admin&node=admin-maintenance--run-full-sync)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- The Extractor Pipeline tab and the record or settings selected there.

## What happens

1. POST /api/admin/maintenance/run with stage=full reserves the full maintenance routine.

## What is saved, and where

- extractor_maintenance_runs and the imported/retired data as each stage completes.

## When it updates

- Queued first; Entries reflect completed import transactions. Also runs nightly at 02:00 UTC.

## Find it in the code

- [docs/app.js:10841 — startMaintenanceRun](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L10841) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [app.py:7728 — admin_start_maintenance](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L7728) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
- [extractor_maintenance.py:355 — queue_maintenance_run](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/extractor_maintenance.py#L355) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/extractor_maintenance.py)
