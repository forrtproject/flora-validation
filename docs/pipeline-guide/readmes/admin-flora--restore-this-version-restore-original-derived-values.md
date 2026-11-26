# Restore this version / Restore original derived values

Restores the selected historical correction state (or removes all overrides). The restore itself is another logged change.

[Open interactive guide](../index.html#view=admin&node=admin-flora--restore-this-version-restore-original-derived-values)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Restores the selected historical correction state (or removes all overrides). The restore itself is another logged change.

## What is saved, and where

- flora_record_changes; live flora_data row where applicable.

## When it updates

- After confirmed restore; same stored-output rules as Save changes.

## Find it in the code

- [docs/app.js:13704 — restoreFloraRecord](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L13704) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [flora_edits.py:692 — restore](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_edits.py#L692) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_edits.py)
- [app.py:7329 — admin_flora_record_restore](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L7329) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
