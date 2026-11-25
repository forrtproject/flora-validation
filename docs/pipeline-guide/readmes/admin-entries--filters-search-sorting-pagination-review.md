# Filters, search, sorting, pagination, Review

Reads matching entries and their details; no study decision is changed.

[Open interactive guide](../index.html#view=admin&node=admin-entries--filters-search-sorting-pagination-review)

Code snapshot: 25 November 2026, `9cc088d`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. Reads matching entries and their details; no study decision is changed.

## What is saved, and where

- No database write.

## When it updates

- On fetch.

## Find it in the code

- [docs/app.js:6406 — fetchAdminEntries](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js#L6406) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js)
- [app.py:5074 — admin_entries](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py#L5074) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py)
- [app.py:5302 — admin_entry_detail](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py#L5302) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py)
