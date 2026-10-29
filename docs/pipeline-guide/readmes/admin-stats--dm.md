# DM

Composes and sends an in-app message to this validator.

[Open interactive guide](../index.html#view=admin&node=admin-stats--dm)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Validator Stats tab and the record or settings selected there.

## What happens

1. Composes and sends an in-app message to this validator.

## What is saved, and where

- validator_messages.

## When it updates

- After Send; the recipient sees it when their inbox refreshes.

## Find it in the code

- [docs/app.js:8159 — openComposeDialog](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L8159) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:5351 — admin_send_message](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L5351) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
