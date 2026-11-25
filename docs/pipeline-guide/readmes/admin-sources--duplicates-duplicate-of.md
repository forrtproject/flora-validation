# Duplicates → Duplicate of …

Names the surviving source row. A reason is requested; cross-type pairing asks for confirmation.

[Open interactive guide](../index.html#view=admin&node=admin-sources--duplicates-duplicate-of)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The Source Records tab and the record or settings selected there.

## What happens

1. Names the surviving source row. A reason is requested; cross-type pairing asks for confirmation.

## What is saved, and where

- source_records.duplicate_status/duplicate_of/review fields and source_record_edits.

## When it updates

- FLoRA grid on next fetch; full CSV/API after pipeline.

## Find it in the code

- [docs/app.js:15432 — resolveSourceDuplicate](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L15432) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
- [source_records_service.py:458 — resolve_duplicate](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/source_records_service.py#L458) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/source_records_service.py)
