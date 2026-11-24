# Export CSV

Downloads rows matching the source filters.

[Open interactive guide](../index.html#view=admin&node=admin-sources--export-csv)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Source Records tab and the record or settings selected there.

## What happens

1. Downloads rows matching the source filters.

## What is saved, and where

- A file downloaded by your browser; no database change.

## When it updates

- At click time.

## Find it in the code

- [docs/app.js:15201 — source export handler](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L15201) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:6849 — admin_source_records_export](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L6849) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
