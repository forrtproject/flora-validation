# Duplicates → Keep — distinct

Records that this input is a separate study and protects it from automatic merging.

[Open interactive guide](../index.html#view=admin&node=admin-sources--duplicates-keep-distinct)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Source Records tab and the record or settings selected there.

## What happens

1. Records that this input is a separate study and protects it from automatic merging.

## What is saved, and where

- source_records duplicate ruling and source_record_edits history.

## When it updates

- FLoRA grid on next fetch; full CSV/API after pipeline; repository after publication.

## Find it in the code

- [docs/app.js:10892 — confirmSourceDistinct](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L10892) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [source_records_service.py:455 — resolve_duplicate](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_records_service.py#L455) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_records_service.py)
