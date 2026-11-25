# Reply

Adds an outbound reply linked to the conversation root, retaining any related judgement link.

[Open interactive guide](../index.html#view=admin&node=admin-messages--reply)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- The Messages tab and the record or settings selected there.

## What happens

1. Adds an outbound reply linked to the conversation root, retaining any related judgement link.

## What is saved, and where

- validator_messages with parent_id and direction=outbound.

## When it updates

- After successful reply; thread updates.

## Find it in the code

- [docs/app.js:11901 — reply handler](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L11901) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [app.py:5750 — admin_reply_to_thread](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L5750) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
