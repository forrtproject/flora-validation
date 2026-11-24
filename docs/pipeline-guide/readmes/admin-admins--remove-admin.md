# Remove admin

Deletes another admin account and revokes its sessions in the same transaction.

[Open interactive guide](../index.html#view=admin&node=admin-admins--remove-admin)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Deletes another admin account and revokes its sessions in the same transaction.

## What is saved, and where

- admins deletion; sessions.revoked_at; security_events.

## When it updates

- After confirmed deletion succeeds.

## Find it in the code

- [docs/app.js:12021 — delete admin handler](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L12021) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:4821 — delete_admin](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L4821) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
