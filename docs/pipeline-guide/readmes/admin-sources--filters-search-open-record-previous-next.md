# Filters / Search / Open record / Previous / Next

Reads the imported row and follows the current filter queue. Links lead to any derived FLoRA records.

[Open interactive guide](../index.html#view=admin&node=admin-sources--filters-search-open-record-previous-next)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Source Records tab and the record or settings selected there.

## What happens

1. Reads the imported row and follows the current filter queue. Links lead to any derived FLoRA records.

## What is saved, and where

- No edits to input fields.

## When it updates

- On fetch.

## Find it in the code

- [docs/app.js:15041 — openSourceRecord](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L15041) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:7596 — admin_source_record_detail](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L7596) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
