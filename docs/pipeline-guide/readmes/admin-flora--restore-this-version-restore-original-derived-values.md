# Restore this version / Restore original derived values

Restores the selected historical correction state (or removes all overrides). The restore itself is another logged change.

[Open interactive guide](../index.html#view=admin&node=admin-flora--restore-this-version-restore-original-derived-values)

Code snapshot: 25 November 2026, `9cc088d`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Restores the selected historical correction state (or removes all overrides). The restore itself is another logged change.

## What is saved, and where

- flora_record_changes; live flora_data row where applicable.

## When it updates

- After confirmed restore; same stored-output rules as Save changes.

## Find it in the code

- [docs/app.js:13729 — restoreFloraRecord](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js#L13729) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js)
- [flora_edits.py:689 — restore](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/flora_edits.py#L689) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/flora_edits.py)
- [app.py:7362 — admin_flora_record_restore](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py#L7362) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py)
