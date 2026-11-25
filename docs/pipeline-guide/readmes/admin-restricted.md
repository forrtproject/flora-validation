# Restricted access

Routes papers that a validator could not access to a chosen person who can review them.

[Open interactive guide](../index.html#view=admin&node=admin-restricted)

Code snapshot: 25 November 2026, `f0cb8de`.

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

POST /api/admin/assign creates or replaces the record's named assignment and marks it open. A record already validated or rejected is refused.

**Saves:** assignments and security_events.

**Visible:** After save; recipient receives it through assigned-work refresh.

## Find it in the code

- [docs/app.js:11089 — fetchAdminRestricted](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L11089) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [docs/app.js:11111 — renderRestricted](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L11111) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [app.py:4286 — admin_restricted](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L4286) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
- [app.py:4322 — admin_assign](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L4322) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
