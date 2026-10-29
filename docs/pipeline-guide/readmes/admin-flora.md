# FLoRA

The prepared view of the current source records: cleaned identifiers, derived outcomes, exclusions, duplicate decisions and saved corrections. This view can be newer than the stored public release.

[Open interactive guide](../index.html#view=admin&node=admin-flora)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- source_records + exclusions + metadata/cache + duplicate rulings + flora_records registry + flora_record_changes.

## What happens

1. The service builds a derived view and attaches permanent IDs.
2. Open a record to correct fields, inspect merged source rows, review checks, or restore a previous correction.
3. Use Download full CSV for what the public API currently serves; Export filtered CSV for the current tab view.

## What is saved, and where

- flora_record_changes stores the correction overlay and complete change history.
- For an existing live stored row, field changes also update flora_data and its metadata revision in the same transaction.
- Preprint rulings are in preprint_dedup_decisions; checks in flora_check_findings.

## When it updates

- Saving a correction refreshes the grid and the open record.
- Existing stored rows get the correction immediately; records not yet stored need the next pipeline. The UI reports which happened.
- Duplicate/withdrawal decisions need the next pipeline to change the full stored release.

## What comes out

- Prepared records, current full CSV, filtered view CSV, correction history, findings and preprint decisions.

## Keep in mind

- Permanent IDs are assigned during pipeline preparation. Filtered export refuses rows without registered IDs.
- The source records and Google Sheets stay as imported; corrections are reapplied over them on later builds.
- A version conflict reloads the record instead of overwriting another admin's changes.

## Buttons and actions

### [Filters / Search / Refresh / Open record](admin-flora--filters-search-refresh-open-record.md)

Fetches a current derived view, details, correction history and check findings.

**Saves:** No study changes.

**Visible:** On request.

### [Save changes](admin-flora--save-changes.md)

PATCH /api/admin/flora/{id} saves changed fields and per-field notes, guarding the record version.

**Saves:** flora_record_changes; changed published fields in flora_data if a live row exists; flora_data_metadata revision.

**Visible:** Grid after success; public API/full CSV immediately for a stored live row, otherwise next pipeline.

### [Revert field to derived value](admin-flora--revert-field-to-derived-value.md)

Removes that field's saved override when Save changes is pressed, so the current derived input value is used again.

**Saves:** New flora_record_changes entry; existing live flora_data row is updated where applicable.

**Visible:** Only after Save changes.

### [Restore this version / Restore original derived values](admin-flora--restore-this-version-restore-original-derived-values.md)

Restores the selected historical correction state (or removes all overrides). The restore itself is another logged change.

**Saves:** flora_record_changes; live flora_data row where applicable.

**Visible:** After confirmed restore; same stored-output rules as Save changes.

### [Confirm outcome / Choose conflicting outcome](admin-flora--confirm-outcome-choose-conflicting-outcome.md)

Settles the conflicting outcomes of merged source rows by confirming the shown outcome or choosing one of the candidates.

**Saves:** flora_record_changes outcome decision, and live flora_data row where applicable.

**Visible:** After success; pipeline retains this correction.

### [Not a problem / Undo dismissal](admin-flora--not-a-problem-undo-dismissal.md)

Adds a reasoned dismissal to a check finding, or reopens it for review. It does not change the scientific record.

**Saves:** flora_check_findings dismissal fields and security_events audit.

**Visible:** After success; matching finding retains its dismissal across later check runs.

### [Download full CSV](admin-flora--download-full-csv.md)

Downloads the live stored dataset: last pipeline's stored rows plus subsequent published corrections.

**Saves:** Browser file only; reads flora_data.

**Visible:** At request time.

### [Export filtered CSV](admin-flora--export-filtered-csv.md)

Downloads the current tab view with its filters. Refused if included records still lack permanent IDs.

**Saves:** Browser file only.

**Visible:** At request time; may differ from the full stored release.

### [Preprint duplicates → Same paper, keep A / keep B](admin-flora--preprint-duplicates-same-paper-keep-a-keep-b.md)

Records which DOI should survive; the discarded DOI becomes an alternative identifier. For original-study DOIs, replications move under the kept DOI.

**Saves:** preprint_dedup_decisions and security_events.

**Visible:** Grid after refetch; stored CSV/API after next pipeline.

### [Different papers — keep both / Undo ruling](admin-flora--different-papers-keep-both-undo-ruling.md)

Keep both overrides automatic merging; Undo removes the explicit ruling so automatic rules apply again.

**Saves:** preprint_dedup_decisions insert/update or deletion, plus security_events audit.

**Visible:** Grid after refetch; stored release after pipeline.

## Find it in the code

- [docs/app.js:9281 — fetchFloraRecords](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L9281) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [flora_service.py:159 — _current](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py#L159) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py)
- [flora_service.py:506 — update_record](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py#L506) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py)
- [app.py:6070 — admin_flora_export](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6070) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
