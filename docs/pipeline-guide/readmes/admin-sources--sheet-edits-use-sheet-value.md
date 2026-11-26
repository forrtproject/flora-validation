# Sheet edits → Use sheet value

Opens the FLoRA editor for the record the edited sheet row feeds, with the sheet's value filled in and a note saying where it came from. Nothing is saved until Save corrections, which logs an ordinary correction. The FLoRA editor's own Sheet edited after import box does the same for one field in place.

[Open interactive guide](../index.html#view=admin&node=admin-sources--sheet-edits-use-sheet-value)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Source Records tab and the record or settings selected there.

## What happens

1. Opens the FLoRA editor for the record the edited sheet row feeds, with the sheet's value filled in and a note saying where it came from. Nothing is saved until Save corrections, which logs an ordinary correction. The FLoRA editor's own Sheet edited after import box does the same for one field in place.

## What is saved, and where

- flora_record_changes and the stored flora_data row, only when Save corrections is clicked; source_records and the sheet are never changed.

## When it updates

- The Sheet edits list and its chip reload when the editor closes; the stored CSV and public API at once on save.

## Find it in the code

- [docs/app.js:15891 — srcDriftUse](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L15891) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [docs/app.js:12998 — floraPrefill](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L12998) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [sheet_drift_service.py:828 — list_findings](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/sheet_drift_service.py#L828) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/sheet_drift_service.py)
