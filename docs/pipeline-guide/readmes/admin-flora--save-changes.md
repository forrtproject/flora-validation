# Save changes

PATCH /api/admin/flora/{id} saves changed fields and per-field notes, guarding the record version.

[Open interactive guide](../index.html#view=admin&node=admin-flora--save-changes)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. PATCH /api/admin/flora/{id} saves changed fields and per-field notes, guarding the record version.

## What is saved, and where

- flora_record_changes; changed published fields in flora_data if a live row exists; flora_data_metadata revision.

## When it updates

- Grid after success; public API/full CSV immediately for a stored live row, otherwise next pipeline.

## Find it in the code

- [docs/app.js:13669 — saveFloraRecord](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L13669) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
- [app.py:7346 — admin_flora_record_update](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py#L7346) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py)
- [flora_edits.py:634 — save](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/flora_edits.py#L634) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/flora_edits.py)
- [flora_edits.py:902 — publish](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/flora_edits.py#L902) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/flora_edits.py)
