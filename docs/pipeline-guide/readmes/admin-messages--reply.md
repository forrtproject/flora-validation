# Reply

Adds an outbound reply linked to the conversation root, retaining any related judgement link.

[Open interactive guide](../index.html#view=admin&node=admin-messages--reply)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Messages tab and the record or settings selected there.

## What happens

1. Adds an outbound reply linked to the conversation root, retaining any related judgement link.

## What is saved, and where

- validator_messages with parent_id and direction=outbound.

## When it updates

- After successful reply; thread updates.

## Find it in the code

- [docs/app.js:8338 — reply handler](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L8338) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:5296 — admin_reply_to_thread](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L5296) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
