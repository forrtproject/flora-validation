# Send invitation

Creates an admin without a password and issues a single-use link, then attempts email delivery. If email fails the response provides a one-time fallback URL to share.

[Open interactive guide](../index.html#view=admin&node=admin-admins--send-invitation)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Creates an admin without a password and issues a single-use link, then attempts email delivery. If email fails the response provides a one-time fallback URL to share.

## What is saved, and where

- admins, auth_links token record, security_events.

## When it updates

- Account exists after commit; it becomes usable after the invitee sets their password.

## Find it in the code

- [docs/app.js:8609 — addAdminAccount](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L8609) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:4258 — create_admin](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L4258) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
