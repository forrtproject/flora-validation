# + New → Send

Validates subject/body and inserts a message for the selected validator.

[Open interactive guide](../index.html#view=admin&node=admin-messages--new-send)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Messages tab and the record or settings selected there.

## What happens

1. Validates subject/body and inserts a message for the selected validator.

## What is saved, and where

- validator_messages.

## When it updates

- After save; recipient sees it when inbox refreshes.

## Find it in the code

- [docs/app.js:12191 — openComposePanel](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L12191) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:5772 — admin_send_message](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L5772) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
