# Approve

In an open Pending Approval row: re-reads the entry and stops if it changed, asks a second time if there is any warning, such as a paper's originals differing on the link or abstract, then POST /api/admin/entries/{id}/approve accepts the agreed values and marks the entry validated. On other filters the Pending approval status pill links to the row instead.

[Open interactive guide](../index.html#view=admin&node=admin-entries--approve)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. In an open Pending Approval row: re-reads the entry and stops if it changed, asks a second time if there is any warning, such as a paper's originals differing on the link or abstract, then POST /api/admin/entries/{id}/approve accepts the agreed values and marks the entry validated. On other filters the Pending approval status pill links to the row instead.

## What is saved, and where

- unvalidated status/admin fields; validated row; admin_decisions.

## When it updates

- Entries immediately; FLoRA input after sync_validated, stored output after a successful pipeline.

## Find it in the code

- [docs/app.js:7793 — _apSendApprove](https://github.com/forrtproject/flora-validation/blob/main/docs/app.js#L7793) · [complete script](https://github.com/forrtproject/flora-validation/blob/main/docs/app.js)
- [app.py:5388 — admin_approve](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L5388) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
