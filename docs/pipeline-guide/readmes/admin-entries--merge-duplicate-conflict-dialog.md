# Merge duplicate (conflict dialog)

If the proposed identity already belongs to another validated record, explicitly keep that survivor and link this duplicate to it. Its added and drafted other originals move to the survivor; if the survivor cannot take them, nothing is saved. The form's corrections and note stay on the duplicate, and a merge cannot be undone.

[Open interactive guide](../index.html#view=admin&node=admin-entries--merge-duplicate-conflict-dialog)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. If the proposed identity already belongs to another validated record, explicitly keep that survivor and link this duplicate to it. Its added and drafted other originals move to the survivor; if the survivor cannot take them, nothing is saved. The form's corrections and note stay on the duplicate, and a merge cannot be undone.

## What is saved, and where

- validated_record_merges; duplicate is rejected in unvalidated, removed from validated, and open assignments completed; survivor preserved.

## When it updates

- After confirmed resolve succeeds; downstream source retirement is triggered on deletion.

## Find it in the code

- [docs/app.js:10066 — confirmValidatedDuplicateMerge](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L10066) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:6643 — admin_resolve duplicate-conflict branch](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L6643) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
