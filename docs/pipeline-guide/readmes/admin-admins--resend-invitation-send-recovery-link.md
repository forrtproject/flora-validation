# Resend invitation / Send recovery link

Issues a fresh invitation for an account without a password, or recovery for an account with one; uses its saved email address.

[Open interactive guide](../index.html#view=admin&node=admin-admins--resend-invitation-send-recovery-link)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Issues a fresh invitation for an account without a password, or recovery for an account with one; uses its saved email address.

## What is saved, and where

- auth_links; possibly an email on an account that previously lacked one; security_events.

## When it updates

- After issuance; it replaces the outstanding link of that kind.

## Find it in the code

- [docs/app.js:12042 — sendAdminLink](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L12042) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:4610 — resend_admin_invite](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L4610) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
