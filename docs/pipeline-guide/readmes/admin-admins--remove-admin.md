# Remove admin

Deletes another admin account and revokes its sessions in the same transaction.

[Open interactive guide](../index.html#view=admin&node=admin-admins--remove-admin)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Deletes another admin account and revokes its sessions in the same transaction.

## What is saved, and where

- admins deletion; sessions.revoked_at; security_events.

## When it updates

- After confirmed deletion succeeds.

## Find it in the code

- [docs/app.js:12046 — delete admin handler](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L12046) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [app.py:4854 — delete_admin](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L4854) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
