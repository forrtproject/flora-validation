# Run pipeline: the background job

The Source Records Run pipeline button queues a durable job. A server worker then refreshes inputs, builds the combined FLoRA data, checks it, and stores the result.

[Open interactive guide](../index.html#view=overview&node=process-queue)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- An authenticated admin request to POST /api/admin/source-sync/dispatch.
- Current sheets, approved records, exclusions, metadata and saved FLoRA decisions.

## What happens

1. Save a queued source_sync_jobs row and return its job ID immediately.
2. The 5-second dispatcher takes an advisory lock and marks one queued job running.
3. Run entry sheets → exclusions → validated records → DOI metadata → FLoRA IDs as separate subprocess stages.
4. If all those stages pass, run prepare_flora.py in a fresh per-run output directory with a 15-minute network-check budget.
5. Store the run's CSV and reports in the database, copy conventional artifacts to server output/, and finish the job.

## What is saved, and where

- source_sync_jobs: requester, status, timestamps, log_text, artifact_csv, report_json, report_markdown and optional recovery_csv.
- The stages update their respective input tables; preparation updates flora_data.
- Server output/ receives a local copy of completed artifacts.

## When it updates

- Queue pickup is normally within the next 5-second tick when a worker is available; total runtime can be minutes.
- The UI polls the job, so queued/running and final result are separate states.
- Download links are bound to that run's durable artifact rather than whichever file currently happens to be on one server.

## What comes out

- A combined stored dataset plus an exact run CSV, JSON report and Markdown report.

## Keep in mind

- Source refresh stages run independently, but any failure prevents the final preparation stage.
- A successful run with warnings can say needs review; that is different from a failed run.
- The website does not commit GitHub or upload to OSF. Use the separate Publish FLoRA workflow for the repository copy.

## Find it in the code

- [app.py:6384 — admin_source_sync_dispatch](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6384) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [source_sync_runner.py:110 — queue_run](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_sync_runner.py#L110) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_sync_runner.py)
- [source_sync_runner.py:269 — _execute](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_sync_runner.py#L269) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_sync_runner.py)
- [source_sync_runner.py:391 — run_queued](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_sync_runner.py#L391) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_sync_runner.py)
- [app.py:6718 — source_sync_dispatcher](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6718) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
