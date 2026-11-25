# Source Records

The input ledger: imported entry-sheet records, approved app records, and published disagreement decisions. Open a row to see its provenance; run the FLoRA pipeline here.

[Open interactive guide](../index.html#view=admin&node=admin-sources)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- source_records from sync_sources.py, sync_validated.py and adjudication publishing.

## What happens

1. Browse imported rows with filters and search; open one to inspect its raw fields and links to derived FLoRA records.
2. Resolve duplicate imports where necessary.
3. Review Sheet edits: imported rows whose sheet row was edited after the import. Take the sheet's value into the FLoRA record, or keep FLoRA's value with a note.
4. Run pipeline to ingest, prepare, store and check the complete FLoRA dataset.

## What is saved, and where

- Duplicate rulings update source_records and append source_record_edits.
- Sheet edits are found by every sync (sheet_drift_findings); a Keep FLoRA value decision is stored on the finding, a sheet value taken in is a FLoRA correction (flora_record_changes).
- Pipeline queue, log and retained artifacts: source_sync_jobs.

## When it updates

- Source Records grid refetches after a watched run ends.
- Pipeline history polls about every 3 seconds while active and visible.
- The FLoRA grid reflects source duplicate rulings on rebuild; stored CSV/API update at the next successful pipeline.

## What comes out

- Filtered source CSV; per-run flora.csv plus Markdown/JSON validation reports and full log.

## Keep in mind

- Source record detail is read-only: correct the derived record in the FLoRA tab.
- Sheet edits never change a sheet or a source record. A row no longer validated in its sheet stays in FLoRA until an exclusion or a duplicate ruling removes it.
- Export CSV here exports source records, not the prepared public dataset.
- Run pipeline does not directly commit a GitHub file. The Publish FLoRA workflow handles the repository copy.
- A failed run can occasionally have committed the dataset before a later artifact step failed; inspect the report storage status and recovery artifact rather than assuming that failed means no database changes.

## Buttons and actions

### [Filters / Search / Open record / Previous / Next](admin-sources--filters-search-open-record-previous-next.md)

Reads the imported row and follows the current filter queue. Links lead to any derived FLoRA records.

**Saves:** No edits to input fields.

**Visible:** On fetch.

### [Export CSV](admin-sources--export-csv.md)

Downloads rows matching the source filters.

**Saves:** A file downloaded by your browser; no database change.

**Visible:** At click time.

### [Run pipeline](admin-sources--run-pipeline.md)

POST /api/admin/source-sync/dispatch queues a server job. A background worker runs the full ingestion/preparation/check sequence.

**Saves:** source_sync_jobs queue/log/artifact fields, source data and prepared dataset during stages.

**Visible:** Queued first; output updates when stages succeed. Also scheduled nightly at 03:00 UTC.

### [Refresh](admin-sources--refresh.md)

Reloads run status/history; it does not launch a run.

**Saves:** No database mutation from this button.

**Visible:** On request.

### [History: CSV / report / full log](admin-sources--history-csv-report-full-log.md)

Downloads the chosen run's retained artifact or displays its complete log. Later edits do not rewrite an older run's CSV.

**Saves:** Browser download only.

**Visible:** The artifact is available once that run produces it.

### [Duplicates → Keep — distinct](admin-sources--duplicates-keep-distinct.md)

Records that this input is a separate study and protects it from automatic merging.

**Saves:** source_records duplicate ruling and source_record_edits history.

**Visible:** FLoRA grid on next fetch; full CSV/API after pipeline; repository after publication.

### [Duplicates → Duplicate of …](admin-sources--duplicates-duplicate-of.md)

Names the surviving source row. A reason is requested; cross-type pairing asks for confirmation.

**Saves:** source_records.duplicate_status/duplicate_of/review fields and source_record_edits.

**Visible:** FLoRA grid on next fetch; full CSV/API after pipeline.

### [Sheet edits → Use sheet value](admin-sources--sheet-edits-use-sheet-value.md)

Opens the FLoRA editor for the record the edited sheet row feeds, with the sheet's value filled in and a note saying where it came from. Nothing is saved until Save corrections, which logs an ordinary correction. The FLoRA editor's own Sheet edited after import box does the same for one field in place.

**Saves:** flora_record_changes and the stored flora_data row, only when Save corrections is clicked; source_records and the sheet are never changed.

**Visible:** The Sheet edits list and its chip reload when the editor closes; the stored CSV and public API at once on save.

### [Sheet edits → Keep FLoRA value / Undo](admin-sources--sheet-edits-keep-flora-value-undo.md)

Records, with a required note, that FLoRA's value is right for one sheet edit, or that a record whose row left the sheet or its validated status stays in FLoRA. Undo withdraws the decision.

**Saves:** sheet_drift_findings kept_by, kept_at, kept_note and the sheet value it was made against; security_events records both the decision and its withdrawal.

**Visible:** Immediately in the view. The decision holds while the sheet keeps that value; a later edit of the cell asks again.

### [Sheet edits → Download CSV](admin-sources--sheet-edits-download-csv.md)

Downloads every sheet edit of the current filter, one row per FLoRA record it touches, with blank decision columns for a coding agent (docs/SHEET_EDITS_FOR_AGENTS.md). apply_sheet_edits.py applies a filled-in file, dry run first.

**Saves:** A file downloaded by your browser; no database change.

**Visible:** At click time.

### [Show resolved / Back to grid](admin-sources--show-resolved-back-to-grid.md)

Includes ruled groups for reconsideration, or returns to the source table.

**Saves:** No write until a new ruling is submitted.

**Visible:** Immediately in the view.

## Find it in the code

- [docs/app.js:12521 — fetchSourceRecords](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L12521) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [docs/app.js:15080 — renderSourceRecord](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L15080) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [app.py:6866 — admin_source_records](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L6866) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
- [source_records_service.py:179 — list_records](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/source_records_service.py#L179) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/source_records_service.py)
- [source_sync_runner.py:340 — failed artifact path with committed-storage recovery](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/source_sync_runner.py#L340) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/source_sync_runner.py)
