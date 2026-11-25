# Link / Unlink validator account

Connects the admin to that person's validator identity for the self-approval rule. Another trusted admin must set your own link.

[Open interactive guide](../index.html#view=admin&node=admin-admins--link-unlink-validator-account)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Connects the admin to that person's validator identity for the self-approval rule. Another trusted admin must set your own link.

## What is saved, and where

- admins.validator_id and security_events.

## When it updates

- After success; future decisions enforce the link.

## Find it in the code

- [docs/app.js:12009 — validator link handler](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L12009) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
- [app.py:4498 — link_admin_validator](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py#L4498) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py)
