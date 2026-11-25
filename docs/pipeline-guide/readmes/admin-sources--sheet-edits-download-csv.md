# Sheet edits → Download CSV

Downloads every sheet edit of the current filter, one row per FLoRA record it touches, with blank decision columns for a coding agent (docs/SHEET_EDITS_FOR_AGENTS.md). apply_sheet_edits.py applies a filled-in file, dry run first.

[Open interactive guide](../index.html#view=admin&node=admin-sources--sheet-edits-download-csv)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The Source Records tab and the record or settings selected there.

## What happens

1. Downloads every sheet edit of the current filter, one row per FLoRA record it touches, with blank decision columns for a coding agent (docs/SHEET_EDITS_FOR_AGENTS.md). apply_sheet_edits.py applies a filled-in file, dry run first.

## What is saved, and where

- A file downloaded by your browser; no database change.

## When it updates

- At click time.

## Find it in the code

- [docs/app.js:16025 — Sheet edits CSV handler](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L16025) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
- [app.py:7551 — admin_sheet_drift_export](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py#L7551) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py)
- [sheet_drift_service.py:1063 — export_csv](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/sheet_drift_service.py#L1063) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/sheet_drift_service.py)
