# Merge duplicate (conflict dialog)

If the proposed identity already belongs to another validated record, explicitly keep that survivor and link this duplicate to it. Its added and drafted other originals move to the survivor; if the survivor cannot take them, nothing is saved. The form's corrections and note stay on the duplicate, and a merge cannot be undone.

[Open interactive guide](../index.html#view=admin&node=admin-entries--merge-duplicate-conflict-dialog)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. If the proposed identity already belongs to another validated record, explicitly keep that survivor and link this duplicate to it. Its added and drafted other originals move to the survivor; if the survivor cannot take them, nothing is saved. The form's corrections and note stay on the duplicate, and a merge cannot be undone.

## What is saved, and where

- validated_record_merges; duplicate is rejected in unvalidated, removed from validated, and open assignments completed; survivor preserved.

## When it updates

- After confirmed resolve succeeds; downstream source retirement is triggered on deletion.

## Find it in the code

- [docs/app.js:10091 — confirmValidatedDuplicateMerge](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L10091) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
- [app.py:6676 — admin_resolve duplicate-conflict branch](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py#L6676) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py)
