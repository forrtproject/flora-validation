# Open conversation

Reads the thread and marks its inbound messages read by the admin.

[Open interactive guide](../index.html#view=admin&node=admin-messages--open-conversation)

Code snapshot: 25 November 2026, `9cc088d`.

## What goes in

- The Messages tab and the record or settings selected there.

## What happens

1. Reads the thread and marks its inbound messages read by the admin.

## What is saved, and where

- validator_messages.is_read_by_admin.

## When it updates

- After opening; unread badge refreshes.

## Find it in the code

- [docs/app.js:11836 — openThread](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js#L11836) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js)
- [app.py:5703 — get admin thread route](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py#L5703) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py)
