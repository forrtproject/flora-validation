# Revert field to derived value

Removes that field's saved override when Save changes is pressed, so the current derived input value is used again.

[Open interactive guide](../index.html#view=admin&node=admin-flora--revert-field-to-derived-value)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Removes that field's saved override when Save changes is pressed, so the current derived input value is used again.

## What is saved, and where

- New flora_record_changes entry; existing live flora_data row is updated where applicable.

## When it updates

- Only after Save changes.

## Find it in the code

- [docs/app.js:13627 — collectFloraEdits](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L13627) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [flora_edits.py:637 — save](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_edits.py#L637) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_edits.py)
