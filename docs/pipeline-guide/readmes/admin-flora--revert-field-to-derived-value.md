# Revert field to derived value

Removes that field's saved override when Save changes is pressed, so the current derived input value is used again.

[Open interactive guide](../index.html#view=admin&node=admin-flora--revert-field-to-derived-value)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Removes that field's saved override when Save changes is pressed, so the current derived input value is used again.

## What is saved, and where

- New flora_record_changes entry; existing live flora_data row is updated where applicable.

## When it updates

- Only after Save changes.

## Find it in the code

- [docs/app.js:13652 — collectFloraEdits](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L13652) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [flora_edits.py:634 — save](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/flora_edits.py#L634) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/flora_edits.py)
