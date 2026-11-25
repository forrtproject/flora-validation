# Published entry sheets

Published Google Sheets are another input route. Their accepted rows are copied into the Source Records collection, alongside the app's approved records.

[Open interactive guide](../index.html#view=overview&node=input-sheets)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- Enabled source definitions in sources.yml: Replications, Reproductions, FReD Replication Success and SCORE 2025 Validation in this checkout.
- Per-source column mappings, UUID column, validation column and accepted status values.

## What happens

1. Fetch each enabled sheet as CSV; check CSV format, headers and row-count floor before accepting it.
2. Skip an unchanged payload by checksum. Filter to accepted validation values, or accept all rows for a source explicitly configured without a validation column.
3. Validate promoted values before writing. Skip missing/invalid UUIDs and all rows sharing a duplicated sheet UUID.
4. Insert only new (source, sheet_row_id) identities. Commit each source independently and record its result.
5. Compare every imported row with the sheet as fetched now, by its sheet UUID (sheet_drift.py), inside a savepoint: each cell that differs from the row as imported becomes a Sheet edits finding; a failed check never undoes the source's inserts.

## What is saved, and where

- source_records stores imported fields, the original raw row, source identity and a friendly display ID.
- source_sync_runs stores per-source timestamps, counts, failures and payload SHA-256.
- sheet_drift_runs stores each sheet-edit check; sheet_drift_findings stores each cell that differs from the import, until the sheet goes back to the imported value.
- snapshots/<source>.csv stores the fetched payload on that executor; the nightly workflow uploads these as 90-day artifacts.

## When it updates

- Source Records → Run pipeline runs this stage on demand.
- GitHub Actions runs Sync entry sheets at 03:00 UTC daily; it can also be started manually.
- New rows appear after source commit and a new grid request.

## What comes out

- A combined Source Records input collection with provenance and sync history.

## Keep in mind

- Sheet sync is insert-only: changing an already-imported UUID's sheet cells does not overwrite the existing database record. The change is now reported under Source Records → Sheet edits, where an admin takes it into FLoRA or keeps FLoRA's value.
- Corrections are made on the derived FLoRA record; this importer does not write changes back to Google Sheets.
- The backup FReD source is disabled in sources.yml.

## Find it in the code

- [sources.yml:34 — accepted_values and source registry](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/sources.yml#L34) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/sources.yml)
- [sync_sources.py:536 — sync_source](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/sync_sources.py#L536) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/sync_sources.py)
- [sync_sources.py:725 — run](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/sync_sources.py#L725) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/sync_sources.py)
- [sync_sources.py:475 — _drift_check](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/sync_sources.py#L475) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/sync_sources.py)
- [sheet_drift.py:219 — compare_source](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/sheet_drift.py#L219) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/sheet_drift.py)
- [source_records_service.py:63 — RECORD_FIELDS](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/source_records_service.py#L63) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/source_records_service.py)
- [.github/workflows/sync-sources.yml:3 — schedule](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/.github/workflows/sync-sources.yml#L3) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/.github/workflows/sync-sources.yml)
