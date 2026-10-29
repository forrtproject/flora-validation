# Preview import

Reads the pinned source and looks up metadata; reports what an import would add/update/keep.

[Open interactive guide](../index.html#view=admin&node=admin-disagreements--preview-import)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Disagreements tab and the record or settings selected there.

## What happens

1. Reads the pinned source and looks up metadata; reports what an import would add/update/keep.

## What is saved, and where

- No adjudication database rows are written by the preview; metadata lookups may be cached.

## When it updates

- After the read/lookup completes.

## Find it in the code

- [docs/adjudication.js:151 — runImport](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/adjudication.js#L151) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/adjudication.js)
- [adjudication/importer.py:417 — run_import](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/importer.py#L417) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/importer.py)
