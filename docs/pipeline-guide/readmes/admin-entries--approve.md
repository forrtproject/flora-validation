# Approve

POST /api/admin/entries/{id}/approve accepts the agreed values and marks the entry validated.

[Open interactive guide](../index.html#view=admin&node=admin-entries--approve)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. POST /api/admin/entries/{id}/approve accepts the agreed values and marks the entry validated.

## What is saved, and where

- unvalidated status/admin fields; validated row; admin_decisions.

## When it updates

- Entries immediately; FLoRA input after sync_validated, stored output after a successful pipeline.

## Find it in the code

- [docs/app.js:5605 — quickApprove](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L5605) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:5388 — admin_approve](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L5388) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
