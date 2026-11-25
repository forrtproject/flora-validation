# Assign / Reassign

POST /api/admin/assign creates or replaces the record's named assignment and marks it open. A record already validated or rejected is refused.

[Open interactive guide](../index.html#view=admin&node=admin-restricted--assign-reassign)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- The Restricted access tab and the record or settings selected there.

## What happens

1. POST /api/admin/assign creates or replaces the record's named assignment and marks it open. A record already validated or rejected is refused.

## What is saved, and where

- assignments and security_events.

## When it updates

- After save; recipient receives it through assigned-work refresh.

## Find it in the code

- [docs/app.js:11151 — assignment handler](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L11151) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [app.py:4322 — admin_assign](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L4322) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
