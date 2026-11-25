# Send to everyone / broadcast

Creates a separate in-app message for each current validator.

[Open interactive guide](../index.html#view=admin&node=admin-messages--send-to-everyone-broadcast)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- The Messages tab and the record or settings selected there.

## What happens

1. Creates a separate in-app message for each current validator.

## What is saved, and where

- One validator_messages row per validator.

## When it updates

- After successful send; each recipient's next inbox poll.

## Find it in the code

- [docs/app.js:12319 — compose submit handler](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L12319) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [app.py:5805 — admin_send_message](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L5805) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
