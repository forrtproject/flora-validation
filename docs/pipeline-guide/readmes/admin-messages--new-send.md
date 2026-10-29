# + New → Send

Validates subject/body and inserts a message for the selected validator.

[Open interactive guide](../index.html#view=admin&node=admin-messages--new-send)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Messages tab and the record or settings selected there.

## What happens

1. Validates subject/body and inserts a message for the selected validator.

## What is saved, and where

- validator_messages.

## When it updates

- After save; recipient sees it when inbox refreshes.

## Find it in the code

- [docs/app.js:8653 — openComposePanel](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L8653) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:5351 — admin_send_message](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L5351) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
