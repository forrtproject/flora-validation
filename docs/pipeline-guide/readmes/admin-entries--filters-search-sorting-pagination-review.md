# Filters, search, sorting, pagination, Review

Reads matching entries and their details; no study decision is changed.

[Open interactive guide](../index.html#view=admin&node=admin-entries--filters-search-sorting-pagination-review)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. Reads matching entries and their details; no study decision is changed.

## What is saved, and where

- No database write.

## When it updates

- On fetch.

## Find it in the code

- [docs/app.js:6406 — fetchAdminEntries](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L6406) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [app.py:5074 — admin_entries](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L5074) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
- [app.py:5302 — admin_entry_detail](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L5302) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
