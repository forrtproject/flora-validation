# Remove admin

Deletes another admin account and revokes its sessions in the same transaction.

[Open interactive guide](../index.html#view=admin&node=admin-admins--remove-admin)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Deletes another admin account and revokes its sessions in the same transaction.

## What is saved, and where

- admins deletion; sessions.revoked_at; security_events.

## When it updates

- After confirmed deletion succeeds.

## Find it in the code

- [docs/app.js:12046 — delete admin handler](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L12046) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
- [app.py:4854 — delete_admin](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py#L4854) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py)
