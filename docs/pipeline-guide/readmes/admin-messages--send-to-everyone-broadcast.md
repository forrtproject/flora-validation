# Send to everyone / broadcast

Creates a separate in-app message for each current validator.

[Open interactive guide](../index.html#view=admin&node=admin-messages--send-to-everyone-broadcast)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Messages tab and the record or settings selected there.

## What happens

1. Creates a separate in-app message for each current validator.

## What is saved, and where

- One validator_messages row per validator.

## When it updates

- After successful send; each recipient's next inbox poll.

## Find it in the code

- [docs/app.js:12294 — compose submit handler](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L12294) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:5772 — admin_send_message](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L5772) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
