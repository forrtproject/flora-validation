# Resend invitation / Send recovery link

Issues a fresh invitation for an account without a password, or recovery for an account with one; uses its saved email address.

[Open interactive guide](../index.html#view=admin&node=admin-admins--resend-invitation-send-recovery-link)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Issues a fresh invitation for an account without a password, or recovery for an account with one; uses its saved email address.

## What is saved, and where

- auth_links; possibly an email on an account that previously lacked one; security_events.

## When it updates

- After issuance; it replaces the outstanding link of that kind.

## Find it in the code

- [docs/app.js:12067 — sendAdminLink](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L12067) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
- [app.py:4643 — resend_admin_invite](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py#L4643) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py)
