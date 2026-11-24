# Send invitation

Creates an admin without a password and issues a single-use link, then attempts email delivery. If email fails the response provides a one-time fallback URL to share.

[Open interactive guide](../index.html#view=admin&node=admin-admins--send-invitation)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Creates an admin without a password and issues a single-use link, then attempts email delivery. If email fails the response provides a one-time fallback URL to share.

## What is saved, and where

- admins, auth_links token record, security_events.

## When it updates

- Account exists after commit; it becomes usable after the invitee sets their password.

## Find it in the code

- [docs/app.js:12147 — addAdminAccount](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L12147) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:4552 — create_admin](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L4552) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
