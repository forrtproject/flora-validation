# Duplicates → Duplicate of …

Names the surviving source row. A reason is requested; cross-type pairing asks for confirmation.

[Open interactive guide](../index.html#view=admin&node=admin-sources--duplicates-duplicate-of)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Source Records tab and the record or settings selected there.

## What happens

1. Names the surviving source row. A reason is requested; cross-type pairing asks for confirmation.

## What is saved, and where

- source_records.duplicate_status/duplicate_of/review fields and source_record_edits.

## When it updates

- FLoRA grid on next fetch; full CSV/API after pipeline.

## Find it in the code

- [docs/app.js:10865 — resolveSourceDuplicate](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L10865) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [source_records_service.py:455 — resolve_duplicate](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_records_service.py#L455) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_records_service.py)
