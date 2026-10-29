# Run pipeline

POST /api/admin/source-sync/dispatch queues a server job. A background worker runs the full ingestion/preparation/check sequence.

[Open interactive guide](../index.html#view=admin&node=admin-sources--run-pipeline)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Source Records tab and the record or settings selected there.

## What happens

1. POST /api/admin/source-sync/dispatch queues a server job. A background worker runs the full ingestion/preparation/check sequence.

## What is saved, and where

- source_sync_jobs queue/log/artifact fields, source data and prepared dataset during stages.

## When it updates

- Queued first; output updates when stages succeed. Also scheduled nightly at 03:00 UTC.

## Find it in the code

- [docs/app.js:10463 — startSourceSync](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L10463) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:6384 — admin_source_sync_dispatch](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6384) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [source_sync_runner.py:110 — queue_run](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_sync_runner.py#L110) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_sync_runner.py)
- [source_sync_runner.py:391 — run_queued](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_sync_runner.py#L391) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_sync_runner.py)
