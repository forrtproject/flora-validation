# Preview import

Reads the pinned source and looks up metadata; reports what an import would add/update/keep.

[Open interactive guide](../index.html#view=admin&node=admin-disagreements--preview-import)

Code snapshot: 25 November 2026, `9cc088d`.

## What goes in

- The Disagreements tab and the record or settings selected there.

## What happens

1. Reads the pinned source and looks up metadata; reports what an import would add/update/keep.

## What is saved, and where

- No adjudication database rows are written by the preview; metadata lookups may be cached.

## When it updates

- After the read/lookup completes.

## Find it in the code

- [docs/adjudication.js:151 — runImport](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/adjudication.js#L151) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/adjudication.js)
- [adjudication/importer.py:414 — run_import](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/adjudication/importer.py#L414) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/adjudication/importer.py)
