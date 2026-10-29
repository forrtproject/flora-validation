# What a successful server save means

A normal API operation groups its database changes into one transaction. They commit together on success, or roll back together if the operation raises an error.

[Open interactive guide](../index.html#view=overview&node=review-transaction)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- A browser API request and a pooled PostgreSQL connection.

## What happens

1. The route enters with db(), which borrows a connection through db_pool.cursor.
2. Related SQL writes run within that transaction. For a judgement this includes the completed slot, review summary, points and consensus effects.
3. Leaving the context successfully commits; an exception rolls back. The connection is then returned to the pool if reusable.
4. The browser reflects the response or retains pending work if it has not obtained confirmation.

## What is saved, and where

- PostgreSQL after commit, independently of the browser tab remaining open.

## When it updates

- Other requests can observe the committed data on their next read.
- The current screen may need its own fetch/poll; a committed database write does not push every open admin tab to a new view.

## What comes out

- A committed operation or a rolled-back operation; a separate pending-browser state can exist when response delivery is uncertain.

## Keep in mind

- Local browser autosave and exported filesystem artifacts do not participate in this database transaction.
- This describes the ordinary API database wrapper; maintenance tools can have their own transaction and stage boundaries.

## Find it in the code

- [app.py:454 — db](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L454) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [db_pool.py:175 — cursor](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_pool.py#L175) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_pool.py)
- [app.py:2625 — judge transaction](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L2625) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
