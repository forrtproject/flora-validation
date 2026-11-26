# Save changes

PATCH /api/admin/flora/{id} saves changed fields and per-field notes, guarding the record version.

[Open interactive guide](../index.html#view=admin&node=admin-flora--save-changes)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. PATCH /api/admin/flora/{id} saves changed fields and per-field notes, guarding the record version.

## What is saved, and where

- flora_record_changes; changed published fields in flora_data if a live row exists; flora_data_metadata revision.

## When it updates

- Grid after success; public API/full CSV immediately for a stored live row, otherwise next pipeline.

## Find it in the code

- [docs/app.js:13644 — saveFloraRecord](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L13644) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:7313 — admin_flora_record_update](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L7313) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
- [flora_edits.py:637 — save](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_edits.py#L637) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_edits.py)
- [flora_edits.py:921 — publish](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_edits.py#L921) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_edits.py)
