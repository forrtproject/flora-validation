# Import rows

Adds missing rows and refreshes only rows nobody has judged; preserves judged rows and deletes nothing.

[Open interactive guide](../index.html#view=admin&node=admin-disagreements--import-rows)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Disagreements tab and the record or settings selected there.

## What happens

1. Adds missing rows and refreshes only rows nobody has judged; preserves judged rows and deletes nothing.

## What is saved, and where

- adjudication.records.

## When it updates

- After import succeeds; counts reload.

## Find it in the code

- [adjudication/importer.py:392 — _write](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/importer.py#L392) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/importer.py)
- [adjudication/api.py:182 — import route](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/api.py#L182) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/api.py)
