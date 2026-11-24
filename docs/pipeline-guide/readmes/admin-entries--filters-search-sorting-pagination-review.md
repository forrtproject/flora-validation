# Filters, search, sorting, pagination, Review

Reads matching entries and their details; no study decision is changed.

[Open interactive guide](../index.html#view=admin&node=admin-entries--filters-search-sorting-pagination-review)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. Reads matching entries and their details; no study decision is changed.

## What is saved, and where

- No database write.

## When it updates

- On fetch.

## Find it in the code

- [docs/app.js:6381 — fetchAdminEntries](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L6381) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:5041 — admin_entries](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L5041) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
- [app.py:5269 — admin_entry_detail](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L5269) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
