# Withdraw from FLoRA

Marks the source row deleted, preserves history and returns the disagreement to approved for editing.

[Open interactive guide](../index.html#view=admin&node=admin-disagreements--withdraw-from-flora)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Disagreements tab and the record or settings selected there.

## What happens

1. Marks the source row deleted, preserves history and returns the disagreement to approved for editing.

## What is saved, and where

- source_records.deleted_at/deleted_reason; adjudication.final withdrawal fields; status=approved.

## When it updates

- Derived view after refresh; stored release at next pipeline.

## Find it in the code

- [adjudication/review.py:574 — withdraw](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/adjudication/review.py#L574) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/adjudication/review.py)
