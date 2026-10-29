# Restricted access

Routes papers that a validator could not access to a chosen person who can review them.

[Open interactive guide](../index.html#view=admin&node=admin-restricted)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- unvalidated.restricted_access, reporting validator and assignments.

## What happens

1. Pick a listed record and choose a validator.
2. Assign or reassign it to that person's assigned-work queue.

## What is saved, and where

- assignments row with status=open, assignee, assigning admin and timestamps; security_events audit.

## When it updates

- The admin list reloads after assignment. The validator assignments poll runs every 30 seconds while visible and catches up after a hidden interval.

## What comes out

- A named assignment for a restricted paper.

## Keep in mind

- Assignment is not approval or publication; the assignee still has to review it.
- Reassign replaces the previous assignment for that record.

## Buttons and actions

### [Assign / Reassign](admin-restricted--assign-reassign.md)

POST /api/admin/assign creates or replaces the record's named assignment and marks it open.

**Saves:** assignments and security_events.

**Visible:** After save; recipient receives it through assigned-work refresh.

## Find it in the code

- [docs/app.js:7532 — fetchAdminRestricted](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L7532) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [docs/app.js:7554 — renderRestricted](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L7554) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:3963 — admin_restricted](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L3963) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [app.py:3999 — admin_assign](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L3999) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
