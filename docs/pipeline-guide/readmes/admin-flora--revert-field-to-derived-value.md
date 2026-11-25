# Revert field to derived value

Removes that field's saved override when Save changes is pressed, so the current derived input value is used again.

[Open interactive guide](../index.html#view=admin&node=admin-flora--revert-field-to-derived-value)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Removes that field's saved override when Save changes is pressed, so the current derived input value is used again.

## What is saved, and where

- New flora_record_changes entry; existing live flora_data row is updated where applicable.

## When it updates

- Only after Save changes.

## Find it in the code

- [docs/app.js:13652 — collectFloraEdits](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L13652) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
- [flora_edits.py:634 — save](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/flora_edits.py#L634) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/flora_edits.py)
