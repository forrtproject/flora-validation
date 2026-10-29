# Trusted toggle

Grants or removes account-management authority for another admin.

[Open interactive guide](../index.html#view=admin&node=admin-admins--trusted-toggle)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Grants or removes account-management authority for another admin.

## What is saved, and where

- admins.trusted and security_events.

## When it updates

- After success.

## Find it in the code

- [docs/app.js:8463 — trusted toggle handler](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L8463) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:4556 — toggle_admin_trusted](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L4556) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
