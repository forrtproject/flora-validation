# Three different CSV downloads

A run CSV, the current full FLoRA CSV and a filtered table export answer different questions. Their bytes can differ after corrections or later runs.

[Open interactive guide](../index.html#view=overview&node=output-downloads)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The chosen export route: a pipeline run, current stored data, or the current admin filters.

## What happens

1. Run CSV: read that job's immutable artifact_csv from source_sync_jobs.
2. Download full CSV: export the current active flora_data rows in publication order.
3. Filtered FLoRA export: export the current derived FLoRA view matching its filters.
4. Source Records export: download raw source collection rows matching its filters.

## What is saved, and where

- A downloaded copy on the user's computer; reading an export does not rebuild the pipeline.
- Run snapshots remain in source_sync_jobs independently of later stored changes.

## When it updates

- Run artifacts remain fixed to the completed run.
- Current full export reflects stored corrections and completed snapshot commits.
- Filtered exports reflect their selected collection and active filters when requested.

## What comes out

- CSV files appropriate for run audit, current publication data or filtered investigation.

## Keep in mind

- Compare like with like: a run CSV is evidence of that run, while the current full export can include later changes.
- A filtered derived view can include rows omitted from the title-required final export.

## Find it in the code

- [source_sync_runner.py:177 — job_artifact](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_sync_runner.py#L177) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_sync_runner.py)
- [app.py:6349 — admin_source_sync_artifact](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6349) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [app.py:6070 — admin_flora_export](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6070) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [flora_service.py:546 — export_csv](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py#L546) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py)
- [app.py:5993 — admin_source_records_export](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L5993) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
