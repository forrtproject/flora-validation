# Assign / Reassign

POST /api/admin/assign creates or replaces the record's named assignment and marks it open. A record already validated or rejected is refused.

[Open interactive guide](../index.html#view=admin&node=admin-restricted--assign-reassign)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Restricted access tab and the record or settings selected there.

## What happens

1. POST /api/admin/assign creates or replaces the record's named assignment and marks it open. A record already validated or rejected is refused.

## What is saved, and where

- assignments and security_events.

## When it updates

- After save; recipient receives it through assigned-work refresh.

## Find it in the code

- [docs/app.js:11126 — assignment handler](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L11126) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:4289 — admin_assign](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L4289) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
