# Open conversation

Reads the thread and marks its inbound messages read by the admin.

[Open interactive guide](../index.html#view=admin&node=admin-messages--open-conversation)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Messages tab and the record or settings selected there.

## What happens

1. Reads the thread and marks its inbound messages read by the admin.

## What is saved, and where

- validator_messages.is_read_by_admin.

## When it updates

- After opening; unread badge refreshes.

## Find it in the code

- [docs/app.js:11811 — openThread](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L11811) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:5670 — get admin thread route](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L5670) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
