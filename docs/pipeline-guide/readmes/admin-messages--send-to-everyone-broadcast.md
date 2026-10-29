# Send to everyone / broadcast

Creates a separate in-app message for each current validator.

[Open interactive guide](../index.html#view=admin&node=admin-messages--send-to-everyone-broadcast)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Messages tab and the record or settings selected there.

## What happens

1. Creates a separate in-app message for each current validator.

## What is saved, and where

- One validator_messages row per validator.

## When it updates

- After successful send; each recipient's next inbox poll.

## Find it in the code

- [docs/app.js:8756 — compose submit handler](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L8756) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:5351 — admin_send_message](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L5351) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
