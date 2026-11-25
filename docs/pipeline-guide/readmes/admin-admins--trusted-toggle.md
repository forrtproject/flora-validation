# Trusted toggle

Grants or removes account-management authority for another admin.

[Open interactive guide](../index.html#view=admin&node=admin-admins--trusted-toggle)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Grants or removes account-management authority for another admin.

## What is saved, and where

- admins.trusted and security_events.

## When it updates

- After success.

## Find it in the code

- [docs/app.js:12026 — trusted toggle handler](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L12026) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
- [app.py:4883 — toggle_admin_trusted](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py#L4883) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py)
