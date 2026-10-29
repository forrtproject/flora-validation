# Filters / Search / Open record / Previous / Next

Reads the imported row and follows the current filter queue. Links lead to any derived FLoRA records.

[Open interactive guide](../index.html#view=admin&node=admin-sources--filters-search-open-record-previous-next)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Source Records tab and the record or settings selected there.

## What happens

1. Reads the imported row and follows the current filter queue. Links lead to any derived FLoRA records.

## What is saved, and where

- No edits to input fields.

## When it updates

- On fetch.

## Find it in the code

- [docs/app.js:10532 — openSourceRecord](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L10532) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:6433 — admin_source_record_detail](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6433) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
