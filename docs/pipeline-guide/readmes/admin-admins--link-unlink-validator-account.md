# Link / Unlink validator account

Connects the admin to that person's validator identity for the self-approval rule. Another trusted admin must set your own link.

[Open interactive guide](../index.html#view=admin&node=admin-admins--link-unlink-validator-account)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Connects the admin to that person's validator identity for the self-approval rule. Another trusted admin must set your own link.

## What is saved, and where

- admins.validator_id and security_events.

## When it updates

- After success; future decisions enforce the link.

## Find it in the code

- [docs/app.js:8446 — validator link handler](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L8446) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:4171 — link_admin_validator](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L4171) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
