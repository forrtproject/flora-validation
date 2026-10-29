# Mark as Resolved

POST /resolve validates the edited type, original, outcome and evidence; saves the final decision. For reproductions both axes are required.

[Open interactive guide](../index.html#view=admin&node=admin-entries--mark-as-resolved)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. POST /resolve validates the edited type, original, outcome and evidence; saves the final decision. For reproductions both axes are required.

## What is saved, and where

- unvalidated.final_* and admin fields; replaces this record's validated row; admin_decisions.

## When it updates

- After commit; then downstream sync/preparation is required.

## Find it in the code

- [docs/app.js:6691 — submitAdminResolve](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L6691) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:5606 — admin_resolve](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L5606) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
