# Complete an assigned restricted study

An administrator can assign a restricted study to a particular validator. That person's submitted answer directly prepares the record for approval, without waiting for two ordinary queue reviews.

[Open interactive guide](../index.html#view=overview&node=review-assignment)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- An open row in assignments for the signed-in validator and the study's effective current values.

## What happens

1. The validator opens the item in My Assignments; the server confirms that the assignment belongs to them.
2. Submit calls /api/assignment-judge synchronously and the interface waits for the response.
3. The server writes corrections and a review summary, completes the assignment, clears restricted_access, and awards double points.
4. A definite eligible answer becomes consensus_reached for admin approval. Explicit uncertainty becomes need_review. A neither-type decision becomes rejected.

## What is saved, and where

- PostgreSQL assignments.status and completed_at; unvalidated.final_* fields, validator_1 summary, validation_status and restricted_access; validators totals.

## When it updates

- The assignment list and stats refresh after successful submission; the validator returns to the normal/hard queue.
- The result appears in the appropriate administrator queue on the next fetch.

## What comes out

- One completed assigned review and a record ready for the next admin decision.

## Keep in mind

- A positive assignment answer is not automatically inserted into validated by this endpoint.
- This path uses the current effective record, including earlier final_* corrections, as its review baseline.

## Find it in the code

- [app.py:2378 — my_assignments](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L2378) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [app.py:2404 — get_assignment](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L2404) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [app.py:2442 — assignment_judge](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L2442) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [docs/app.js:4802 — assignment submission branch](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L4802) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [db_schema.sql:810 — assignments](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql#L810) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql)
