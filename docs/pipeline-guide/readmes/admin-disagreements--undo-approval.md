# Undo approval

Removes an approved final answer that has never been published and returns it to awaiting approval.

[Open interactive guide](../index.html#view=admin&node=admin-disagreements--undo-approval)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Disagreements tab and the record or settings selected there.

## What happens

1. Removes an approved final answer that has never been published and returns it to awaiting approval.

## What is saved, and where

- Deletes adjudication.final and changes adjudication.records status.

## When it updates

- After confirmation succeeds.

## Find it in the code

- [adjudication/review.py:302 — undo_approval](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/review.py#L302) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/review.py)
