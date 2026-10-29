# Open conversation

Reads the thread and marks its inbound messages read by the admin.

[Open interactive guide](../index.html#view=admin&node=admin-messages--open-conversation)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Messages tab and the record or settings selected there.

## What happens

1. Reads the thread and marks its inbound messages read by the admin.

## What is saved, and where

- validator_messages.is_read_by_admin.

## When it updates

- After opening; unread badge refreshes.

## Find it in the code

- [docs/app.js:8273 — openThread](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L8273) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:5249 — get admin thread route](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L5249) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
