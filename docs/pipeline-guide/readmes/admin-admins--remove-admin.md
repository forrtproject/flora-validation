# Remove admin

Deletes another admin account and revokes its sessions in the same transaction.

[Open interactive guide](../index.html#view=admin&node=admin-admins--remove-admin)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Deletes another admin account and revokes its sessions in the same transaction.

## What is saved, and where

- admins deletion; sessions.revoked_at; security_events.

## When it updates

- After confirmed deletion succeeds.

## Find it in the code

- [docs/app.js:8483 — delete admin handler](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L8483) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:4527 — delete_admin](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L4527) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
