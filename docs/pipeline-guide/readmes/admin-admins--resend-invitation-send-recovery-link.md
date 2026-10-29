# Resend invitation / Send recovery link

Issues a fresh invitation for an account without a password, or recovery for an account with one; uses its saved email address.

[Open interactive guide](../index.html#view=admin&node=admin-admins--resend-invitation-send-recovery-link)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Issues a fresh invitation for an account without a password, or recovery for an account with one; uses its saved email address.

## What is saved, and where

- auth_links; possibly an email on an account that previously lacked one; security_events.

## When it updates

- After issuance; it replaces the outstanding link of that kind.

## Find it in the code

- [docs/app.js:8504 — sendAdminLink](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L8504) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:4316 — resend_admin_invite](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L4316) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
