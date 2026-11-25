# Approve

In an open Pending Approval row: re-reads the entry and stops if it changed, asks a second time if there is any warning, such as a paper's originals differing on the link or abstract, then POST /api/admin/entries/{id}/approve accepts the agreed values and marks the entry validated. On other filters the Pending approval status pill links to the row instead.

[Open interactive guide](../index.html#view=admin&node=admin-entries--approve)

Code snapshot: 25 November 2026, `9cc088d`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. In an open Pending Approval row: re-reads the entry and stops if it changed, asks a second time if there is any warning, such as a paper's originals differing on the link or abstract, then POST /api/admin/entries/{id}/approve accepts the agreed values and marks the entry validated. On other filters the Pending approval status pill links to the row instead.

## What is saved, and where

- unvalidated status/admin fields; validated row; admin_decisions.

## When it updates

- Entries immediately; FLoRA input after sync_validated, stored output after a successful pipeline.

## Find it in the code

- [docs/app.js:7950 — _apSendApprove](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js#L7950) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js)
- [app.py:5842 — admin_approve](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py#L5842) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py)
