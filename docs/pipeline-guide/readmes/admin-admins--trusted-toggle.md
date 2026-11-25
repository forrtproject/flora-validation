# Trusted toggle

Grants or removes account-management authority for another admin.

[Open interactive guide](../index.html#view=admin&node=admin-admins--trusted-toggle)

Code snapshot: 25 November 2026, `9cc088d`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Grants or removes account-management authority for another admin.

## What is saved, and where

- admins.trusted and security_events.

## When it updates

- After success.

## Find it in the code

- [docs/app.js:12026 — trusted toggle handler](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js#L12026) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js)
- [app.py:4883 — toggle_admin_trusted](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py#L4883) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py)
