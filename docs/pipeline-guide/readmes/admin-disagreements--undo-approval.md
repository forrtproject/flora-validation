# Undo approval

Removes an approved final answer that has never been published and returns it to awaiting approval.

[Open interactive guide](../index.html#view=admin&node=admin-disagreements--undo-approval)

Code snapshot: 25 November 2026, `9cc088d`.

## What goes in

- The Disagreements tab and the record or settings selected there.

## What happens

1. Removes an approved final answer that has never been published and returns it to awaiting approval.

## What is saved, and where

- Deletes adjudication.final and changes adjudication.records status.

## When it updates

- After confirmation succeeds.

## Find it in the code

- [adjudication/review.py:386 — undo_approval](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/adjudication/review.py#L386) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/adjudication/review.py)
