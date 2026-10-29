# Flag for Review / Send back for review

Moves an eligible record to need_review. Sending back an auto-validated record also removes its approved row.

[Open interactive guide](../index.html#view=admin&node=admin-entries--flag-for-review-send-back-for-review)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. Moves an eligible record to need_review. Sending back an auto-validated record also removes its approved row.

## What is saved, and where

- unvalidated status/admin note; validated deletion for auto-validated rows; admin_decisions.

## When it updates

- After success; the validated-source trigger marks its source row deleted at commit.

## Find it in the code

- [docs/app.js:6613 — Send back handler](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L6613) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:5518 — admin_flag_review](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L5518) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
