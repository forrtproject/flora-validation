# Withdraw from FLoRA

Marks the source row deleted, preserves history and returns the disagreement to approved for editing.

[Open interactive guide](../index.html#view=admin&node=admin-disagreements--withdraw-from-flora)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The Disagreements tab and the record or settings selected there.

## What happens

1. Marks the source row deleted, preserves history and returns the disagreement to approved for editing.

## What is saved, and where

- source_records.deleted_at/deleted_reason; adjudication.final withdrawal fields; status=approved.

## When it updates

- Derived view after refresh; stored release at next pipeline.

## Find it in the code

- [adjudication/review.py:567 — withdraw](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/review.py#L567) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/review.py)
