# Restore this version / Restore original derived values

Restores the selected historical correction state (or removes all overrides). The restore itself is another logged change.

[Open interactive guide](../index.html#view=admin&node=admin-flora--restore-this-version-restore-original-derived-values)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Restores the selected historical correction state (or removes all overrides). The restore itself is another logged change.

## What is saved, and where

- flora_record_changes; live flora_data row where applicable.

## When it updates

- After confirmed restore; same stored-output rules as Save changes.

## Find it in the code

- [docs/app.js:9874 — restoreFloraRecord](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L9874) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [flora_edits.py:579 — restore](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_edits.py#L579) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_edits.py)
- [app.py:6293 — admin_flora_record_restore](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6293) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
