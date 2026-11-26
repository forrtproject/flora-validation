# Duplicates → Keep — distinct

Records that this input is a separate study and protects it from automatic merging.

[Open interactive guide](../index.html#view=admin&node=admin-sources--duplicates-keep-distinct)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Source Records tab and the record or settings selected there.

## What happens

1. Records that this input is a separate study and protects it from automatic merging.

## What is saved, and where

- source_records duplicate ruling and source_record_edits history.

## When it updates

- FLoRA grid on next fetch; full CSV/API after pipeline; repository after publication.

## Find it in the code

- [docs/app.js:15434 — confirmSourceDistinct](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L15434) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [source_records_service.py:458 — resolve_duplicate](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/source_records_service.py#L458) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/source_records_service.py)
