# Import rows

Adds missing rows and refreshes only rows nobody has judged; preserves judged rows and deletes nothing.

[Open interactive guide](../index.html#view=admin&node=admin-disagreements--import-rows)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- The Disagreements tab and the record or settings selected there.

## What happens

1. Adds missing rows and refreshes only rows nobody has judged; preserves judged rows and deletes nothing.

## What is saved, and where

- adjudication.records.

## When it updates

- After import succeeds; counts reload.

## Find it in the code

- [adjudication/importer.py:392 — _write](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/adjudication/importer.py#L392) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/adjudication/importer.py)
- [adjudication/api.py:194 — import route](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/adjudication/api.py#L194) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/adjudication/api.py)
