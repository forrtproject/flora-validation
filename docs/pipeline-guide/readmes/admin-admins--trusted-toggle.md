# Trusted toggle

Grants or removes account-management authority for another admin.

[Open interactive guide](../index.html#view=admin&node=admin-admins--trusted-toggle)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Grants or removes account-management authority for another admin.

## What is saved, and where

- admins.trusted and security_events.

## When it updates

- After success.

## Find it in the code

- [docs/app.js:12001 — trusted toggle handler](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L12001) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:4850 — toggle_admin_trusted](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L4850) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
