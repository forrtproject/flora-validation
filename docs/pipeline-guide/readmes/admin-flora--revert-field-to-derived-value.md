# Revert field to derived value

Removes that field's saved override when Save changes is pressed, so the current derived input value is used again.

[Open interactive guide](../index.html#view=admin&node=admin-flora--revert-field-to-derived-value)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Removes that field's saved override when Save changes is pressed, so the current derived input value is used again.

## What is saved, and where

- New flora_record_changes entry; existing live flora_data row is updated where applicable.

## When it updates

- Only after Save changes.

## Find it in the code

- [docs/app.js:9797 — collectFloraEdits](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L9797) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [flora_edits.py:527 — save](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_edits.py#L527) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_edits.py)
