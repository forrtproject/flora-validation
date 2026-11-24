# Flag for Review / Send back for review

Moves an eligible record to need_review. Sending back an auto-validated record also removes its approved row.

[Open interactive guide](../index.html#view=admin&node=admin-entries--flag-for-review-send-back-for-review)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. Moves an eligible record to need_review. Sending back an auto-validated record also removes its approved row.

## What is saved, and where

- unvalidated status/admin note; validated deletion for auto-validated rows; admin_decisions.

## When it updates

- After success; the validated-source trigger marks its source row deleted at commit.

## Find it in the code

- [docs/app.js:10021 — Send back handler](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L10021) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:6275 — admin_flag_review](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L6275) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
