# Merge duplicate (conflict dialog)

If the proposed identity already belongs to another validated record, explicitly keep that survivor and link this duplicate to it.

[Open interactive guide](../index.html#view=admin&node=admin-entries--merge-duplicate-conflict-dialog)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. If the proposed identity already belongs to another validated record, explicitly keep that survivor and link this duplicate to it.

## What is saved, and where

- validated_record_merges; duplicate is rejected in unvalidated, removed from validated, and open assignments completed; survivor preserved.

## When it updates

- After confirmed resolve succeeds; downstream source retirement is triggered on deletion.

## Find it in the code

- [docs/app.js:6654 — confirmValidatedDuplicateMerge](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L6654) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:5821 — admin_resolve duplicate-conflict branch](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L5821) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
