# DM

Composes and sends an in-app message to this validator.

[Open interactive guide](../index.html#view=admin&node=admin-stats--dm)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Validator Stats tab and the record or settings selected there.

## What happens

1. Composes and sends an in-app message to this validator.

## What is saved, and where

- validator_messages.

## When it updates

- After Send; the recipient sees it when their inbox refreshes.

## Find it in the code

- [docs/app.js:11697 — openComposeDialog](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L11697) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:5772 — admin_send_message](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L5772) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
