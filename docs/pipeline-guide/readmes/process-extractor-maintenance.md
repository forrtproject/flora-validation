# Extractor maintenance: sync, report, retire

This pipeline refreshes the human-review input and reports or retires obsolete extractor pairs. It is separate from the pipeline that builds the final FLoRA dataset.

[Open interactive guide](../index.html#view=overview&node=process-extractor-maintenance)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- A scheduled or admin-queued maintenance run.
- A verified extractor snapshot and, for retirement, its matching retired_pairs.csv manifest.

## What happens

1. Take a PostgreSQL advisory lock so one executor owns maintenance.
2. Run sync_csv.py to download, validate, import and verify promotion.
3. For a full run, backfill OpenAlex work IDs after a successful sync; an enrichment failure is a warning.
4. Run find_orphans.py against the verified snapshot to describe missing pairs without deleting them.
5. When automatic retirement is enabled, archive and retire only eligible, untouched pairs explicitly listed in the source manifest.

## What is saved, and where

- extractor_maintenance_runs stores queued/running/final status, per-stage results, logs and safety evidence.
- retired_records stores the complete archived record before eligible review records and their dependent data are removed.
- A combined server log defaults to logs/extractor_maintenance.log.

## When it updates

- Nightly at 02:00 UTC, or when an admin-queued run is picked up by the 10-second dispatcher.
- Each stage updates the run history; dependent stages are skipped when prerequisites fail.

## What comes out

- An updated validation queue, a read-only orphan summary, and an explicit retirement report.

## Keep in mind

- Missing from the latest CSV does not mean delete: many missing pairs are intentionally withheld validated work.
- Retirement is disabled by EXTRACTOR_AUTO_RETIRE=0/off and is capped by EXTRACTOR_MAX_RETIRE_PERCENT (default 15%). Human-worked records are protected.
- This pipeline does not build output/flora.csv.

## Find it in the code

- [extractor_maintenance.py:959 — run_pipeline](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/extractor_maintenance.py#L959) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/extractor_maintenance.py)
- [extractor_maintenance.py:1406 — run_scheduled](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/extractor_maintenance.py#L1406) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/extractor_maintenance.py)
- [csv_to_db.py:1587 — run_retire](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/csv_to_db.py#L1587) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/csv_to_db.py)
- [csv_to_db.py:1430 — _apply_retire_batch](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/csv_to_db.py#L1430) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/csv_to_db.py)
- [app.py:8070 — extractor_maintenance_dispatcher](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L8070) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
