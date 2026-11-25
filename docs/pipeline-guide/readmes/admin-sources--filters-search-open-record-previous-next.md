# Filters / Search / Open record / Previous / Next

Reads the imported row and follows the current filter queue. Links lead to any derived FLoRA records.

[Open interactive guide](../index.html#view=admin&node=admin-sources--filters-search-open-record-previous-next)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- The Source Records tab and the record or settings selected there.

## What happens

1. Reads the imported row and follows the current filter queue. Links lead to any derived FLoRA records.

## What is saved, and where

- No edits to input fields.

## When it updates

- On fetch.

## Find it in the code

- [docs/app.js:15066 — openSourceRecord](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L15066) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [app.py:7629 — admin_source_record_detail](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L7629) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
