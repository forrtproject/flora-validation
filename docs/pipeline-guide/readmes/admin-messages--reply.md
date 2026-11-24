# Reply

Adds an outbound reply linked to the conversation root, retaining any related judgement link.

[Open interactive guide](../index.html#view=admin&node=admin-messages--reply)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Messages tab and the record or settings selected there.

## What happens

1. Adds an outbound reply linked to the conversation root, retaining any related judgement link.

## What is saved, and where

- validator_messages with parent_id and direction=outbound.

## When it updates

- After successful reply; thread updates.

## Find it in the code

- [docs/app.js:11876 — reply handler](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L11876) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:5717 — admin_reply_to_thread](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L5717) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
