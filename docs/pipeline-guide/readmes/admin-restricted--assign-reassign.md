# Assign / Reassign

POST /api/admin/assign creates or replaces the record's named assignment and marks it open.

[Open interactive guide](../index.html#view=admin&node=admin-restricted--assign-reassign)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Restricted access tab and the record or settings selected there.

## What happens

1. POST /api/admin/assign creates or replaces the record's named assignment and marks it open.

## What is saved, and where

- assignments and security_events.

## When it updates

- After save; recipient receives it through assigned-work refresh.

## Find it in the code

- [docs/app.js:7594 — assignment handler](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L7594) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:3999 — admin_assign](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L3999) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
