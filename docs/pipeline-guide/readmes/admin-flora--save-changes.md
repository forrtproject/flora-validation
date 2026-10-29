# Save changes

PATCH /api/admin/flora/{id} saves changed fields and per-field notes, guarding the record version.

[Open interactive guide](../index.html#view=admin&node=admin-flora--save-changes)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. PATCH /api/admin/flora/{id} saves changed fields and per-field notes, guarding the record version.

## What is saved, and where

- flora_record_changes; changed published fields in flora_data if a live row exists; flora_data_metadata revision.

## When it updates

- Grid after success; public API/full CSV immediately for a stored live row, otherwise next pipeline.

## Find it in the code

- [docs/app.js:9814 — saveFloraRecord](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L9814) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:6277 — admin_flora_record_update](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6277) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [flora_edits.py:527 — save](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_edits.py#L527) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_edits.py)
- [flora_edits.py:759 — publish](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_edits.py#L759) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_edits.py)
