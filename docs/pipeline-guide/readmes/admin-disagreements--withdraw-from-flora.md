# Withdraw from FLoRA

Marks the source row deleted, preserves history and returns the disagreement to approved for editing.

[Open interactive guide](../index.html#view=admin&node=admin-disagreements--withdraw-from-flora)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Disagreements tab and the record or settings selected there.

## What happens

1. Marks the source row deleted, preserves history and returns the disagreement to approved for editing.

## What is saved, and where

- source_records.deleted_at/deleted_reason; adjudication.final withdrawal fields; status=approved.

## When it updates

- Derived view after refresh; stored release at next pipeline.

## Find it in the code

- [adjudication/review.py:451 — withdraw](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/review.py#L451) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/review.py)
