# Link / Unlink validator account

Connects the admin to that person's validator identity for the self-approval rule. Another trusted admin must set your own link.

[Open interactive guide](../index.html#view=admin&node=admin-admins--link-unlink-validator-account)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Connects the admin to that person's validator identity for the self-approval rule. Another trusted admin must set your own link.

## What is saved, and where

- admins.validator_id and security_events.

## When it updates

- After success; future decisions enforce the link.

## Find it in the code

- [docs/app.js:11984 — validator link handler](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L11984) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:4465 — link_admin_validator](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L4465) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
