# Link / Unlink validator account

Connects the admin to that person's validator identity for the self-approval rule. Another trusted admin must set your own link.

[Open interactive guide](../index.html#view=admin&node=admin-admins--link-unlink-validator-account)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Connects the admin to that person's validator identity for the self-approval rule. Another trusted admin must set your own link.

## What is saved, and where

- admins.validator_id and security_events.

## When it updates

- After success; future decisions enforce the link.

## Find it in the code

- [docs/app.js:12009 — validator link handler](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L12009) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [app.py:4498 — link_admin_validator](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L4498) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
