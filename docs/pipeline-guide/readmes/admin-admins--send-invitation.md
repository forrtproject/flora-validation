# Send invitation

Creates an admin without a password and issues a single-use link, then attempts email delivery. If email fails the response provides a one-time fallback URL to share.

[Open interactive guide](../index.html#view=admin&node=admin-admins--send-invitation)

Code snapshot: 25 November 2026, `9cc088d`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Creates an admin without a password and issues a single-use link, then attempts email delivery. If email fails the response provides a one-time fallback URL to share.

## What is saved, and where

- admins, auth_links token record, security_events.

## When it updates

- Account exists after commit; it becomes usable after the invitee sets their password.

## Find it in the code

- [docs/app.js:12172 — addAdminAccount](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js#L12172) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js)
- [app.py:4585 — create_admin](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py#L4585) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py)
